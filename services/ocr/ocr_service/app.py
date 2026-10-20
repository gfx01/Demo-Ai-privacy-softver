"""HTTP API of the OCR service.

``PUT /process`` implements Open WebUI 0.12.3's external document loader
contract (``CONTENT_EXTRACTION_ENGINE=external``): the raw file is the request
body, ``Content-Type`` and ``X-Filename`` describe it, and the answer is
``{"page_content": str, "metadata": {...}}``. The privacy gateway uses the
same endpoint for images and files attached to chat requests.

The service only extracts text; masking happens later in the gateway. It
never logs request bodies, file names or the user headers Open WebUI sends.
Extraction runs in a killable worker process (see worker.py).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .extract import ExtractionError, TooLargeError, UnsupportedTypeError
from .worker import DEFAULT_FACTORY, EngineError, ExtractionWorker, WorkerCrashError

log = logging.getLogger("ocr_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings
    # OCR_ENGINE_FACTORY exists for the test suite (a fake engine).
    app.state.worker = await asyncio.to_thread(ExtractionWorker, settings, os.environ.get("OCR_ENGINE_FACTORY", DEFAULT_FACTORY))
    app.state.slots = asyncio.Semaphore(1)
    log.info("OCR service ready (engine=%s)", app.state.worker.engine_name)
    yield
    app.state.worker.close()


app = FastAPI(title="privacy-hub OCR", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


@app.get("/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "engine": request.app.state.worker.engine_name}


@app.put("/process")
async def process(request: Request):
    settings: Settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(token.encode(), settings.api_key.encode()):
        return _error(401, "invalid or missing API key")

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_bytes:
        return _error(413, f"file larger than {settings.max_bytes} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > settings.max_bytes:
            return _error(413, f"file larger than {settings.max_bytes} bytes")
    if not body:
        return _error(422, "empty file")

    content_type = request.headers.get("content-type", "")
    filename = unquote(request.headers.get("x-filename", ""))
    worker: ExtractionWorker = request.app.state.worker
    try:
        # The slot is held until the worker has finished or has been killed.
        async with request.app.state.slots:
            result = await asyncio.to_thread(worker.run, bytes(body), content_type, filename, settings.timeout_s)
    except UnsupportedTypeError as exc:
        return _error(415, str(exc))
    except TooLargeError as exc:
        return _error(413, str(exc))
    except ExtractionError as exc:
        return _error(422, str(exc))
    except WorkerCrashError:
        log.error("extraction worker crashed; restarted")
        return _error(422, "the file could not be processed (the extractor stopped)")
    except TimeoutError:
        log.error("extraction timed out; worker restarted")
        return _error(504, f"text extraction took longer than {settings.timeout_s} s")
    except (EngineError, RuntimeError) as exc:
        log.error("extraction failed: %s", type(exc).__name__)
        return _error(503, "OCR engine unavailable")
    return {
        "page_content": result.text,
        "metadata": {
            "source": "privacy-hub-ocr",
            "engine": worker.engine_name if result.ocr_pages else "none",
            "kind": result.kind,
            "pages": result.pages,
            "ocr_pages": result.ocr_pages,
        },
    }
