"""Images and files attached to chat messages become text before masking.

With an OCR service configured (``PRIVACY_HUB_OCR_URL``), every inline
``image_url`` part (``data:image/...;base64,...``, as Open WebUI sends chat
images) and every inline ``file`` part (``file_data``) is sent to the
internal OCR service and replaced by a text part. That text then goes
through the same masking as the rest of the message, so the model never
receives the image or file itself.

Fail closed: if the OCR service is unreachable, slow or answers with an
error, the request is blocked. Remote image URLs and provider file ids are
refused: the gateway does not fetch URLs (SSRF) and cannot inspect files
stored at the provider.

Open WebUI resends the whole conversation on every turn, so results are
kept in process memory, keyed by the SHA-256 of the attachment, for at most
the mapping TTL. The cache holds unmasked OCR text; it never leaves the
LiteLLM process.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .messages import UnsupportedContentError

# ASCII only: the MIME type becomes an HTTP header of the OCR request.
DATA_URL = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)?(?:;[\w.+-]+=[^;,]*)*;base64,", re.IGNORECASE | re.ASCII)

LABELS = {
    "hu": {"image": "[Csatolt kép szövege (OCR)]", "file": "[Csatolt fájl szövege: {name}]", "empty": "(nem található benne szöveg)"},
    "en": {"image": "[Text of attached image (OCR)]", "file": "[Text of attached file: {name}]", "empty": "(no text found)"},
}


class OcrUnavailableError(RuntimeError):
    """The OCR service could not produce a trustworthy result: block the request."""


@dataclass
class _Attachment:
    content: list
    index: int
    kind: str  # "image" | "file"
    mime: str
    data: bytes
    filename: str


class OcrClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_s: float,
        max_bytes: int,
        cache_ttl_s: float,
        cache_entries: int = 64,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s),
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )
        self.max_bytes = max_bytes
        self.cache_ttl_s = cache_ttl_s
        self.cache_entries = cache_entries
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()

    async def convert(self, messages: object, language: str) -> int:
        """Replace attachment parts in place with text parts; return how many."""
        attachments = self._find(messages)
        labels = LABELS.get(language, LABELS["en"])
        for att in attachments:
            text = await self._text(att)
            if att.kind == "image":
                header = labels["image"]
            else:
                header = labels["file"].format(name=att.filename or att.mime)
            att.content[att.index] = {"type": "text", "text": f"{header}\n{text or labels['empty']}"}
        return len(attachments)

    def _find(self, messages: object) -> list[_Attachment]:
        found: list[_Attachment] = []
        if not isinstance(messages, list):
            return found  # collect_request_slots reports the malformed request
        for msg in messages:
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for j, part in enumerate(content):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    ref = part.get("image_url")
                    url = ref.get("url") if isinstance(ref, dict) else ref
                    mime, data = self._decode(url, "image")
                    found.append(_Attachment(content, j, "image", mime, data, ""))
                elif part.get("type") == "file":
                    ref = part.get("file") if isinstance(part.get("file"), dict) else {}
                    if ref.get("file_id") or not ref.get("file_data"):
                        raise UnsupportedContentError("file parts must carry the file inline (file_data); file ids cannot be inspected")
                    mime, data = self._decode(ref["file_data"], "file")
                    name = ref.get("filename") if isinstance(ref.get("filename"), str) else ""
                    found.append(_Attachment(content, j, "file", mime, data, name[:200]))
        return found

    def _decode(self, url: object, kind: str) -> tuple[str, bytes]:
        if not isinstance(url, str):
            raise UnsupportedContentError(f"{kind} part without data")
        m = DATA_URL.match(url)
        if not m:
            if url.startswith(("http://", "https://")):
                raise UnsupportedContentError("remote image URLs are not fetched by the privacy gateway; attach the image itself")
            raise UnsupportedContentError(f"{kind} must be an inline base64 data URL")
        payload = url[m.end():]
        if len(payload) * 3 // 4 > self.max_bytes:
            raise UnsupportedContentError(f"attachment larger than {self.max_bytes} bytes")
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise UnsupportedContentError("attachment is not valid base64") from exc
        if not data:
            raise UnsupportedContentError("empty attachment")
        return (m.group("mime") or "application/octet-stream").lower(), data

    async def _text(self, att: _Attachment) -> str:
        key = hashlib.sha256(att.mime.encode() + b"\0" + att.data).hexdigest()
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            self._cache.move_to_end(key)
            return hit[1]
        headers = {"Content-Type": att.mime}
        if att.filename:
            headers["X-Filename"] = quote(att.filename)
        try:
            resp = await self._client.put("/process", content=att.data, headers=headers)
        except httpx.HTTPError as exc:
            raise OcrUnavailableError(f"OCR service unreachable: {type(exc).__name__}") from exc
        if resp.status_code in (413, 415, 422):
            raise UnsupportedContentError(f"attachment cannot be read: {self._detail(resp)}")
        if resp.status_code != 200:
            raise OcrUnavailableError(f"OCR service returned HTTP {resp.status_code}")
        try:
            text = resp.json()["page_content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise OcrUnavailableError("OCR service returned a malformed response") from exc
        if not isinstance(text, str):
            raise OcrUnavailableError("OCR service returned a malformed response")
        self._cache[key] = (now + self.cache_ttl_s, text)
        while len(self._cache) > self.cache_entries:
            self._cache.popitem(last=False)
        return text

    @staticmethod
    def _detail(resp: httpx.Response) -> str:
        try:
            detail = resp.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        return str(detail)[:200] if detail else f"HTTP {resp.status_code}"

    async def aclose(self) -> None:
        await self._client.aclose()
