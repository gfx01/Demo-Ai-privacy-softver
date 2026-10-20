"""LiteLLM guardrail: mask before the provider call, restore afterwards.

Registered in the LiteLLM config as::

    guardrails:
      - guardrail_name: privacy-hub
        litellm_params:
          guardrail: privacy_hub.guardrail.PrivacyHubGuardrail
          mode: [pre_call, post_call]
          default_on: true

Design rules (fail closed):

* ``should_run_guardrail`` ignores per-key/team opt-outs and request-level
  guardrail selection: the guardrail runs on every request.
* Only chat completions are supported; every other call type is rejected
  because its payload is not inspected.
* Any error while masking (analyzer down/slow/malformed, store or audit
  unavailable, unsupported content) raises -> LiteLLM returns an error and
  never calls the provider.
* With an OCR service configured, inline images and files are replaced by
  their extracted text first (``privacy_hub.attachments``) and masked like
  any other text; an OCR failure blocks the request.
* The mapping is kept out of the request dict (LiteLLM logs request metadata);
  only a random request id is put there. The mapping itself goes to the
  encrypted, TTL-bound store.

The three hook methods must be defined on THIS class: LiteLLM 1.105.1 detects
overrides via the leaf class ``__dict__`` (``ProxyLogging._callback_capabilities``),
so a subclass that merely inherits them would silently lose streaming restore.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from .analyzer_client import AnalyzerClient, AnalyzerError
from .attachments import OcrClient, OcrUnavailableError
from .audit import AuditEvent, AuditSink, AuditUnavailableError, LogAuditSink, PostgresAuditSink
from .config import Config, FatalConfigError
from .crypto import MappingCipher
from .db import Database
from .masking import StreamingRestorer, build_restorer
from .masking.model import Mapping
from .messages import (
    UnsupportedContentError,
    check_request_params,
    collect_definition_slots,
    collect_request_slots,
    restore_json_text,
    restore_message_in_place,
)
from .org_settings import OrgSettings, SettingsProvider, SettingsUnavailableError
from .store import InMemoryMappingStore, MappingStore, RedisMappingStore

log = logging.getLogger("privacy_hub.guardrail")

REQUEST_ID_KEY = "privacy_hub_request_id"
SUPPORTED_CALL_TYPES = frozenset({"acompletion", "completion"})
RUN_ON = frozenset({GuardrailEventHooks.pre_call, GuardrailEventHooks.post_call, GuardrailEventHooks.pre_mcp_call})


def _blocked(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def _meta(data: dict) -> dict:
    for key in ("metadata", "litellm_metadata"):
        if isinstance(data.get(key), dict):
            return data[key]
    data["metadata"] = {}
    return data["metadata"]


class PrivacyHubGuardrail(CustomGuardrail):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.default_on is not True:
            raise FatalConfigError("privacy-hub guardrail must be configured with default_on: true")
        self.cfg = Config.from_env()
        self.cipher = MappingCipher(self.cfg.mapping_key)
        self.store: MappingStore = (
            InMemoryMappingStore(self.cipher)
            if self.cfg.store_url.startswith("memory://")
            else RedisMappingStore(self.cipher, self.cfg.store_url)
        )
        self.analyzer = AnalyzerClient(self.cfg.analyzer_url, self.cfg.analyzer_token, self.cfg.analyzer_timeout_s)
        self.ocr: OcrClient | None = (
            OcrClient(self.cfg.ocr_url, self.cfg.ocr_token, self.cfg.ocr_timeout_s, self.cfg.ocr_max_bytes, self.cfg.mapping_ttl_s)
            if self.cfg.ocr_url
            else None
        )
        self.db = Database(self.cfg.database_url) if self.cfg.database_url else None
        self.settings = SettingsProvider(self.cfg, self.db)
        self.audit: AuditSink = PostgresAuditSink(self.db) if self.db else LogAuditSink()
        log.info("privacy-hub guardrail initialised (%s)", self.cfg)

    # -- mandatory execution ---------------------------------------------
    def should_run_guardrail(self, data, event_type: GuardrailEventHooks) -> bool:  # type: ignore[override]
        """Always run for request and response hooks, regardless of key/team
        metadata (``disable_global_guardrails``, ``opted_out_global_guardrails``)
        or request-level guardrail lists."""
        return event_type in RUN_ON

    # -- identity helpers --------------------------------------------------
    def _org_id(self, uak: Any) -> str:
        for source in (getattr(uak, "metadata", None), getattr(uak, "team_metadata", None)):
            if isinstance(source, dict) and source.get("organisation_id"):
                return str(source["organisation_id"])
        return self.cfg.default_org

    def _event(self, uak: Any, org_id: str, request_id: str, event: str, outcome: str, **kw: Any) -> AuditEvent:
        return AuditEvent(
            organisation_id=org_id,
            request_id=request_id,
            event=event,
            outcome=outcome,
            user_id=getattr(uak, "user_id", None),
            team_id=getattr(uak, "team_id", None),
            key_alias=getattr(uak, "key_alias", None),
            end_user_id=getattr(uak, "end_user_id", None),
            **kw,
        )

    async def _audit(self, event: AuditEvent, *, required: bool) -> None:
        try:
            await self.audit.record(event)
        except AuditUnavailableError:
            log.error("audit write failed for request %s", event.request_id)
            if required and self.cfg.audit_required:
                raise _blocked(503, "privacy_hub_audit_unavailable", "Audit log unavailable; request blocked.")

    # The request id travels in request metadata, which a client can also
    # write: ids are HMAC-tagged so a forged one never selects a mapping to
    # load or delete, nor ends up in the audit log.
    def _new_request_id(self) -> str:
        rid = uuid.uuid4().hex
        return f"{rid}.{self._request_id_tag(rid)}"

    def _request_id_tag(self, rid: str) -> str:
        return hmac.new(self.cfg.mapping_key, b"request-id|" + rid.encode(), hashlib.sha256).hexdigest()[:20]

    def _request_id(self, data: Any) -> str | None:
        meta = data.get("metadata") if isinstance(data, dict) else None
        if not isinstance(meta, dict) and isinstance(data, dict):
            meta = data.get("litellm_metadata")
        value = meta.get(REQUEST_ID_KEY) if isinstance(meta, dict) else None
        if not isinstance(value, str) or value.count(".") != 1:
            return None
        rid, tag = value.split(".")
        return value if hmac.compare_digest(tag, self._request_id_tag(rid)) else None

    def _pseudonymous_user(self, user: str) -> str:
        digest = hmac.new(self.cfg.mapping_key, user.encode(), hashlib.sha256).hexdigest()[:24]
        return f"u_{digest}"

    # -- request -----------------------------------------------------------
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):  # noqa: C901
        started = time.perf_counter()
        request_id = self._new_request_id()
        org_id = self._org_id(user_api_key_dict)
        model = data.get("model") if isinstance(data, dict) else None
        meta = _meta(data)
        meta[REQUEST_ID_KEY] = request_id  # always overwrite anything the client sent

        def ev(outcome: str, settings: OrgSettings | None = None, **kw: Any) -> AuditEvent:
            return self._event(
                user_api_key_dict,
                org_id,
                request_id,
                "request",
                outcome,
                model=model,
                mode=settings.mode if settings else None,
                strategy=settings.strategy if settings else None,
                language=settings.language if settings else None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                **kw,
            )

        if str(call_type) not in SUPPORTED_CALL_TYPES:
            await self._audit(ev("blocked_unsupported_route", error_class=str(call_type)), required=False)
            raise _blocked(403, "privacy_hub_unsupported", f"Call type '{call_type}' is not allowed through the privacy gateway.")

        try:
            settings = await self.settings.get(org_id)
        except SettingsUnavailableError:
            await self._audit(ev("blocked_error", error_class="SettingsUnavailable"), required=False)
            raise _blocked(503, "privacy_hub_unavailable", "Privacy settings unavailable; request blocked.")
        is_canary = model == self.cfg.canary_model
        if is_canary:
            settings = OrgSettings(org_id, "mask", settings.strategy, settings.language)

        try:
            check_request_params(data, self.cfg.extra_allowed_params)
            if self.ocr is not None:
                # images/files -> text parts, masked below like any other text
                await self.ocr.convert(data.get("messages"), settings.language)
            slots = collect_request_slots(data.get("messages"), self.cfg.allow_non_text_content)
            slots += collect_definition_slots(data)
        except UnsupportedContentError as exc:
            await self._audit(ev("blocked_unsupported_content", settings, error_class="UnsupportedContent"), required=False)
            raise _blocked(400, "privacy_hub_unsupported_content", str(exc))
        except OcrUnavailableError as exc:
            log.error("OCR failed, blocking request %s: %s", request_id, exc)
            await self._audit(ev("blocked_error", settings, error_class="OcrUnavailable"), required=False)
            raise _blocked(503, "privacy_hub_unavailable", "OCR unavailable; request with attachments blocked (fail closed).")

        texts = [s.get() for s in slots]
        try:
            result = await self.analyzer.mask(
                texts,
                language=settings.language,
                strategy=settings.strategy,
                entities=None,
                disabled_entities=settings.disabled_entities,
                org_id=org_id,
            )
        except AnalyzerError as exc:
            log.error("masking failed, blocking request %s: %s", request_id, exc)
            await self._audit(ev("blocked_error", settings, error_class="AnalyzerError"), required=False)
            raise _blocked(503, "privacy_hub_unavailable", "PII detection unavailable; request blocked (fail closed).")

        counts = result.entity_counts
        if settings.mode == "strict" and result.entity_total > 0 and model != self.cfg.preview_model:
            summary = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
            await self._audit(ev("blocked_strict", settings, entity_counts=counts), required=False)
            raise _blocked(
                400,
                "privacy_hub_sensitive_data",
                f"Sensitive data detected ({summary}); strict mode blocks this request.",
            )

        for slot, masked in zip(slots, result.masked_texts, strict=True):
            slot.set(masked)
        if isinstance(data.get("user"), str) and data["user"]:
            data["user"] = self._pseudonymous_user(data["user"])
        # LiteLLM 1.105.1 forwards a client-supplied metadata.user_id to
        # Anthropic (llms/anthropic/chat/transformation.py), taking precedence
        # over the pseudonymised `user`: pseudonymise it the same way.
        for holder in (data.get("metadata"), data.get("litellm_metadata")):
            if isinstance(holder, dict) and holder.get("user_id") not in (None, ""):
                uid = holder["user_id"]
                holder["user_id"] = self._pseudonymous_user(uid if isinstance(uid, str) else json.dumps(uid, sort_keys=True))

        if len(result.mapping):
            try:
                await self.store.put(request_id, result.mapping, self.cfg.mapping_ttl_s, binding=org_id)
            except Exception as exc:  # noqa: BLE001
                log.error("mapping store unavailable, blocking request %s: %s", request_id, type(exc).__name__)
                await self._audit(ev("blocked_error", settings, error_class="StoreUnavailable"), required=False)
                raise _blocked(503, "privacy_hub_unavailable", "Mapping store unavailable; request blocked.")

        outcome = "preview" if model == self.cfg.preview_model else "forwarded"
        await self._audit(ev(outcome, settings, entity_counts=counts), required=True)
        return data

    # -- response (non-streaming) -------------------------------------------
    async def _load_mapping(self, data: dict, uak: Any) -> tuple[str | None, Mapping | None]:
        request_id = self._request_id(data)
        if not request_id:
            return None, None
        try:
            return request_id, await self.store.get(request_id, binding=self._org_id(uak))
        except Exception as exc:  # noqa: BLE001
            log.error("mapping store read failed for %s: %s", request_id, type(exc).__name__)
            return request_id, None

    async def _finish(self, uak: Any, data: dict, request_id: str, outcome: str, **kw: Any) -> None:
        if not self.cfg.keep_mapping_after_response:
            try:
                await self.store.delete(request_id)
            except Exception:  # noqa: BLE001 - TTL still expires it
                log.warning("mapping delete failed for %s (TTL will expire it)", request_id)
        await self._audit(
            self._event(uak, self._org_id(uak), request_id, "response", outcome, model=data.get("model"), **kw),
            required=False,
        )

    async def async_post_call_success_hook(self, data: dict, user_api_key_dict, response):
        request_id, mapping = await self._load_mapping(data, user_api_key_dict)
        if request_id is None:
            return response
        if data.get("model") == self.cfg.preview_model or mapping is None:
            outcome = "preview" if mapping is not None else "completed_no_mapping"
            await self._finish(user_api_key_dict, data, request_id, outcome)
            return response
        restorer = build_restorer(mapping)
        leftover = 0
        for choice in getattr(response, "choices", None) or []:
            message = getattr(choice, "message", None)
            if message is not None:
                restore_message_in_place(message, restorer.restore)
                content = getattr(message, "content", None)
                if isinstance(content, str):
                    leftover += len(restorer.leftover_tokens(content))
        await self._finish(
            user_api_key_dict,
            data,
            request_id,
            "completed",
            restored_total=sum(restorer.restored.values()),
            leftover_tokens=leftover,
        )
        return response

    # -- response (streaming) -------------------------------------------------
    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data: dict
    ) -> AsyncGenerator[Any, None]:
        request_id, mapping = await self._load_mapping(request_data, user_api_key_dict)
        if request_id is None or mapping is None or request_data.get("model") == self.cfg.preview_model:
            async for chunk in response:
                yield chunk
            if request_id is not None:
                outcome = "preview" if request_data.get("model") == self.cfg.preview_model else "completed_no_mapping"
                await self._finish(user_api_key_dict, request_data, request_id, outcome)
            return

        restorers: dict[int, StreamingRestorer] = {}
        # Tool-call argument fragments are buffered per (choice, tool call) and
        # released, restored and correctly JSON-escaped, with the finish chunk.
        tool_args: dict[int, dict[int, list[str]]] = {}
        last_chunk: Any = None
        try:
            async for chunk in response:
                for choice in getattr(chunk, "choices", None) or []:
                    idx = getattr(choice, "index", 0) or 0
                    sr = restorers.get(idx)
                    if sr is None:
                        sr = restorers[idx] = StreamingRestorer(build_restorer(mapping))
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    emitted = sr.feed(content) if isinstance(content, str) and content else ""
                    for call in getattr(delta, "tool_calls", None) or []:
                        fn = getattr(call, "function", None)
                        fragment = getattr(fn, "arguments", None) if fn is not None else None
                        if fragment:
                            tool_args.setdefault(idx, {}).setdefault(getattr(call, "index", 0) or 0, []).append(fragment)
                            fn.arguments = ""
                    if getattr(choice, "finish_reason", None):
                        emitted += sr.flush()
                        self._release_tool_calls(delta, tool_args.pop(idx, {}), sr.restorer.restore)
                    if isinstance(content, str) or emitted:
                        delta.content = emitted
                last_chunk = chunk
                yield chunk
            for idx, sr in restorers.items():
                tail = sr.flush()
                pending_calls = tool_args.pop(idx, {})
                if (tail or pending_calls) and last_chunk is not None:
                    extra = self._tail_chunk(last_chunk, idx, tail)
                    for c in extra.choices:
                        self._release_tool_calls(c.delta, pending_calls, sr.restorer.restore)
                    yield extra
        finally:
            restored = sum(sum(sr.restorer.restored.values()) for sr in restorers.values())
            await self._finish(user_api_key_dict, request_data, request_id, "completed", restored_total=restored)

    @staticmethod
    def _release_tool_calls(delta: Any, calls: dict[int, list[str]], restore) -> None:
        if not calls:
            return
        from litellm.types.utils import ChatCompletionDeltaToolCall, Function

        released = [
            ChatCompletionDeltaToolCall(index=i, function=Function(arguments=restore_json_text("".join(parts), restore)))
            for i, parts in sorted(calls.items())
        ]
        delta.tool_calls = (getattr(delta, "tool_calls", None) or []) + released

    @staticmethod
    def _tail_chunk(template: Any, index: int, text: str) -> Any:
        chunk = copy.deepcopy(template)
        chunk.choices = [c for c in chunk.choices if (getattr(c, "index", 0) or 0) == index][:1] or chunk.choices[:1]
        for c in chunk.choices:
            c.finish_reason = None
            c.delta.content = text
            if hasattr(c.delta, "tool_calls"):
                c.delta.tool_calls = None
        return chunk

    # -- failures ------------------------------------------------------------
    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict, traceback_str=None):
        request_id = self._request_id(request_data)
        if request_id and not isinstance(original_exception, HTTPException):
            await self._finish(
                user_api_key_dict,
                request_data,
                request_id,
                "provider_error",
                error_class=type(original_exception).__name__,
            )
        return None
