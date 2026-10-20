"""OCR service settings from environment variables (no secrets in code)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

ENGINES = ("tesseract", "surya")


class ConfigError(RuntimeError):
    """Misconfiguration: the service refuses to start."""


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "")
    return default if value == "" else value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return default if value == "" else int(value)


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    engine: str = "tesseract"
    tessdata_dir: str = "/app/tessdata"
    tesseract_lang: str = "hun+eng"
    surya_license_accepted: bool = False
    surya_inference_url: str = ""
    max_bytes: int = 25 * 1024 * 1024
    max_pages: int = 50
    max_pixels: int = 40_000_000
    max_unzipped_bytes: int = 100 * 1024 * 1024
    pdf_dpi: int = 200
    min_text_layer_chars: int = 20
    timeout_s: int = 180
    fix_hu_accents: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls(
            api_key=os.environ.get("OCR_API_KEY", ""),
            engine=os.environ.get("OCR_ENGINE", "tesseract").strip().lower(),
            tessdata_dir=os.environ.get("OCR_TESSDATA_DIR", "/app/tessdata"),
            tesseract_lang=os.environ.get("OCR_TESSERACT_LANG", "hun+eng"),
            surya_license_accepted=_bool("OCR_SURYA_LICENSE_ACCEPTED"),
            surya_inference_url=os.environ.get("SURYA_INFERENCE_URL", ""),
            max_bytes=_int("OCR_MAX_BYTES", 25 * 1024 * 1024),
            max_pages=_int("OCR_MAX_PAGES", 50),
            max_pixels=_int("OCR_MAX_PIXELS", 40_000_000),
            max_unzipped_bytes=_int("OCR_MAX_UNZIPPED_BYTES", 100 * 1024 * 1024),
            pdf_dpi=_int("OCR_PDF_DPI", 200),
            min_text_layer_chars=_int("OCR_MIN_TEXT_LAYER_CHARS", 20),
            timeout_s=_int("OCR_TIMEOUT_S", 180),
            fix_hu_accents=_bool("OCR_FIX_HU_ACCENTS", True),
        )
        s.validate()
        return s

    def validate(self) -> None:
        problems = []
        if len(self.api_key) < 16:
            problems.append("OCR_API_KEY must be set (>= 16 characters)")
        if self.engine not in ENGINES:
            problems.append(f"OCR_ENGINE must be one of {', '.join(ENGINES)}")
        if self.engine == "surya":
            if not self.surya_license_accepted:
                problems.append(
                    "OCR_ENGINE=surya needs OCR_SURYA_LICENSE_ACCEPTED=true. The Surya model weights are "
                    "NOT open for commercial use: free only for organisations under USD 5M revenue and "
                    "USD 5M funding that do not compete with Datalab; otherwise a paid licence is needed. "
                    "Read the weights licence (MODEL_LICENSE in the surya repository) and docs/LIMITATIONS.md first."
                )
            if not self.surya_inference_url:
                problems.append(
                    "OCR_ENGINE=surya needs SURYA_INFERENCE_URL (the self-hosted vLLM/llama.cpp server); "
                    "without it Surya would try to start Docker containers and download weights"
                )
        if not 1 <= self.max_pages <= 1000:
            problems.append("OCR_MAX_PAGES must be between 1 and 1000")
        if not 72 <= self.pdf_dpi <= 600:
            problems.append("OCR_PDF_DPI must be between 72 and 600")
        if problems:
            raise ConfigError("; ".join(problems))
