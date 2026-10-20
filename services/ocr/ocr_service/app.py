"""HTTP API of the OCR service.

``PUT /process`` implements Open WebUI 0.12.3's external document loader
contract (``CONTENT_EXTRACTION_ENGINE=external``): the raw file is the request
body, ``Content-Type`` and ``X-Filename`` describe it, and the answer is
``{"page_content": str, "metadata": {...}}``. The privacy gateway uses the
same endpoint for images and files attached to chat requests.

The service only extracts text; masking happens later in the gateway. It
never logs request bodies, file names or the user headers Open WebUI sends.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image

from .config import Settings
from .engines import make_engine
from .extract import ExtractionError, TooLargeError, UnsupportedTypeError, extract

log = logging.getLogger("ocr_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    Image.MAX_IMAGE_PIXELS = settings.max_pixels
    app.state.settings = settings
    app.state.engine = make_engine(settings)
    app.state.slots = asyncio.Semaphore(1)
    log.info("OCR service ready (engine=%s)", settings.engine)
    yield


app = FastAPI(title="privacy-hub OCR", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


@app.get("/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "engine": request.app.state.engine.name}


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
    try:
        async with request.app.state.slots:
            result = await asyncio.wait_for(
                asyncio.to_thread(extract, bytes(body), content_type, filename, request.app.state.engine, settings),
                timeout=settings.timeout_s,
            )
    except UnsupportedTypeError as exc:
        return _error(415, str(exc))
    except TooLargeError as exc:
        return _error(413, str(exc))
    except ExtractionError as exc:
        return _error(422, str(exc))
    except TimeoutError:
        return _error(504, f"text extraction took longer than {settings.timeout_s} s")
    except Exception as exc:  # noqa: BLE001 - engine/model server failures
        log.error("extraction failed: %s", type(exc).__name__)
        return _error(503, f"OCR engine unavailable ({type(exc).__name__})")
    return {
        "page_content": result.text,
        "metadata": {
            "source": "privacy-hub-ocr",
            "engine": request.app.state.engine.name if result.ocr_pages else "none",
            "kind": result.kind,
            "pages": result.pages,
            "ocr_pages": result.ocr_pages,
        },
    }
