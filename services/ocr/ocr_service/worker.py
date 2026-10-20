"""Extraction runs in a separate worker process that can be killed.

A thread cannot be stopped: a file crafted to take hours (an XLSX with a row
index of 10^12, a PDF with pathological content) would keep running after
the HTTP timeout and, a few uploads later, jam the service. A native parser
crash (pdfium, Tesseract) would take the whole service down. The worker
process runs one extraction at a time; on timeout or crash it is killed and
a fresh one started, and the slot is only freed once the old one is gone.
"""

from __future__ import annotations

import importlib
import logging
import multiprocessing
import threading
from typing import Any

from .config import Settings
from .extract import ExtractionError, Extracted, TooLargeError, UnsupportedTypeError, extract

log = logging.getLogger("ocr_service")
EXPECTED_ERRORS = {cls.__name__: cls for cls in (ExtractionError, TooLargeError, UnsupportedTypeError)}
DEFAULT_FACTORY = "ocr_service.engines:make_engine"
STARTUP_TIMEOUT_S = 300


class WorkerCrashError(Exception):
    """The worker died while processing (native crash, out of memory)."""


class EngineError(Exception):
    """The OCR engine failed (e.g. the Surya model server is unreachable)."""


def _load(factory: str):
    module, _, name = factory.partition(":")
    return getattr(importlib.import_module(module), name)


def _main(conn, settings: Settings, factory: str) -> None:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = settings.max_pixels
    try:
        engine = _load(factory)(settings)
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        conn.send(("init_error", f"{type(exc).__name__}: {exc}"))
        return
    conn.send(("ready", engine.name))
    while True:
        try:
            data, content_type, filename = conn.recv()
        except EOFError:
            return
        try:
            conn.send(("ok", extract(data, content_type, filename, engine, settings)))
        except Exception as exc:  # noqa: BLE001 - mapped to HTTP errors by the parent
            conn.send(("error", type(exc).__name__, str(exc)[:300]))


class ExtractionWorker:
    def __init__(self, settings: Settings, factory: str = DEFAULT_FACTORY) -> None:
        self.settings = settings
        self.factory = factory
        self._ctx = multiprocessing.get_context("spawn")
        self._proc: Any = None
        self._conn: Any = None
        self.engine_name = ""
        # The pipe carries one request and one reply at a time; the HTTP layer
        # serialises calls too, but a second caller must never read the
        # reply meant for the first (one user's document text for another).
        self._lock = threading.Lock()
        self._start()

    def _start(self) -> None:
        parent, child = self._ctx.Pipe()
        proc = self._ctx.Process(target=_main, args=(child, self.settings, self.factory), daemon=True)
        proc.start()
        child.close()
        if not parent.poll(STARTUP_TIMEOUT_S):
            proc.kill()
            proc.join(10)
            raise RuntimeError("OCR worker did not start in time")
        try:
            status, detail = parent.recv()
        except EOFError as exc:
            raise RuntimeError("OCR worker exited during start-up") from exc
        if status != "ready":
            proc.join(5)
            log.error("OCR engine could not be initialised: %s", detail)
            raise RuntimeError(f"OCR engine could not be initialised: {detail}")
        self._proc, self._conn, self.engine_name = proc, parent, detail

    def _restart(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.join(10)
            self._conn.close()
        self._proc = self._conn = None
        self._start()

    def alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def ensure_running(self) -> None:
        with self._lock:
            if not self.alive():
                self._restart()

    def run(self, data: bytes, content_type: str, filename: str, timeout_s: float) -> Extracted:
        """Blocking; one extraction at a time."""
        with self._lock:
            return self._run(data, content_type, filename, timeout_s)

    def _run(self, data: bytes, content_type: str, filename: str, timeout_s: float) -> Extracted:
        if not self.alive():
            self._restart()
        try:
            self._conn.send((data, content_type, filename))
            finished = self._conn.poll(timeout_s)
            reply = self._conn.recv() if finished else None
        except (EOFError, OSError) as exc:  # pipe closed: the worker died
            self._restart()
            raise WorkerCrashError from exc
        if not finished:
            self._restart()
            raise TimeoutError
        if reply[0] == "ok":
            return reply[1]
        _, name, message = reply
        if name in EXPECTED_ERRORS:
            raise EXPECTED_ERRORS[name](message)
        raise EngineError(name)

    def close(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.join(10)
