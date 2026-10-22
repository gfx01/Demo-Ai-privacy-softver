"""OCR engines: Tesseract (default) and Surya (opt-in, licence-restricted weights).

Both take Pillow images that the service itself decoded and return one text
per image. Neither engine ever sees the uploaded file bytes: the Tesseract
wheel bundles old image codecs (libpng/libtiff/libwebp), so it only gets raw
grayscale pixels; Surya gets RGB images and talks to a self-hosted
OpenAI-compatible model server (vLLM or llama.cpp) on the internal network.
"""

from __future__ import annotations

import threading
from typing import Protocol

from PIL import Image

from .config import Settings
from .text import html_to_text


class OcrEngine(Protocol):
    name: str

    def ocr(self, images: list[Image.Image], dpi: int) -> list[str]: ...


class TesseractEngine:
    name = "tesseract"

    def __init__(self, tessdata_dir: str, lang: str) -> None:
        import tesserocr

        # Fails at startup (not on the first request) if a language file is missing.
        self._api = tesserocr.PyTessBaseAPI(path=tessdata_dir, lang=lang, oem=tesserocr.OEM.LSTM_ONLY)
        self._lock = threading.Lock()  # one PyTessBaseAPI is not thread-safe

    def ocr(self, images: list[Image.Image], dpi: int) -> list[str]:
        texts = []
        with self._lock:
            for image in images:
                gray = image.convert("L")
                pixels = gray.tobytes()  # must stay referenced until GetUTF8Text returns
                self._api.SetImageBytes(pixels, gray.width, gray.height, 1, gray.width)
                self._api.SetSourceResolution(dpi)
                texts.append(self._api.GetUTF8Text())
        return texts


class SuryaEngine:
    """Surya 2 full-page OCR through the surya-ocr client (0.22.1 API).

    Only built into the image with OCR_WITH_SURYA=1. SURYA_INFERENCE_URL must
    point at the self-hosted model server; Settings.validate enforces it, so
    Surya never spawns containers or downloads weights itself."""

    name = "surya"

    def __init__(self) -> None:
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor

        self._predictor = RecognitionPredictor(SuryaInferenceManager())
        self._lock = threading.Lock()

    def ocr(self, images: list[Image.Image], dpi: int) -> list[str]:  # noqa: ARG002
        with self._lock:
            pages = self._predictor([image.convert("RGB") for image in images], full_page=True)
        return [page_text(page) for page in pages]


def page_text(page) -> str:
    """Plain text of a surya PageOCRResult: blocks in reading order, HTML
    stripped; skipped (pictures) and failed blocks are left out."""
    blocks = sorted((b for b in page.blocks if not b.skipped and not b.error), key=lambda b: b.reading_order)
    return "\n\n".join(t for t in (html_to_text(b.html) for b in blocks) if t)


def make_engine(settings: Settings) -> OcrEngine:
    if settings.engine == "surya":
        return SuryaEngine()
    return TesseractEngine(settings.tessdata_dir, settings.tesseract_lang)
