"""Turn an uploaded file into plain text.

Supported: PDF (text layer, OCR for pages without one), images (PNG, JPEG,
TIFF incl. multi-page, WebP, GIF, BMP), DOCX, XLSX, plain text, Markdown,
CSV, JSON and HTML. Anything else is refused (HTTP 415), never passed on
unread.
"""

from __future__ import annotations

import io
import math
import zipfile
from dataclasses import dataclass

from PIL import Image, ImageOps

from .config import Settings
from .engines import OcrEngine
from .text import fix_hungarian_accents, html_to_text, tidy

IMAGE_FORMATS = ["PNG", "JPEG", "TIFF", "WEBP", "GIF", "BMP"]
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "text/x-markdown", "application/json", "text/tab-separated-values"}
TEXT_EXTENSIONS = {"txt", "md", "markdown", "csv", "tsv", "json", "log"}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
HTML_EXTENSIONS = {"html", "htm", "xhtml"}
# Cells read from a spreadsheet; an empty row counts as one, so a sheet with
# an absurd row index cannot loop for hours.
MAX_SPREADSHEET_CELLS = 500_000


class ExtractionError(Exception):
    """The file could not be read (damaged, encrypted, wrong type): HTTP 422."""


class UnsupportedTypeError(Exception):
    """File type not supported: HTTP 415."""


class TooLargeError(Exception):
    """Over a page, pixel or size limit: HTTP 413."""


@dataclass
class Extracted:
    text: str
    kind: str
    pages: int = 0
    ocr_pages: int = 0


def sniff(data: bytes, content_type: str, filename: str) -> str:
    """Decide the file kind from its first bytes; the declared type and the
    file name extension are only used for text formats and BMP."""
    ctype = content_type.split(";", 1)[0].strip().lower()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or head[:4] in (b"II*\x00", b"MM\x00*")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        or head[:6] in (b"GIF87a", b"GIF89a")
        or (head[:2] == b"BM" and (ctype == "image/bmp" or ext == "bmp"))
    ):
        return "image"
    if head.startswith(b"PK\x03\x04"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile as exc:
            raise ExtractionError("damaged ZIP container") from exc
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        raise UnsupportedTypeError("ZIP-based format not supported (only DOCX and XLSX)")
    if ctype in HTML_TYPES or ext in HTML_EXTENSIONS:
        return "html"
    if ctype in TEXT_TYPES or ext in TEXT_EXTENSIONS or ctype.startswith("text/"):
        return "text"
    raise UnsupportedTypeError(f"file type not supported: {ctype or 'unknown'}")


def extract(data: bytes, content_type: str, filename: str, engine: OcrEngine, settings: Settings) -> Extracted:
    kind = sniff(data, content_type, filename)
    if kind == "pdf":
        result = _pdf(data, engine, settings)
    elif kind == "image":
        result = _image(data, engine, settings)
    elif kind == "docx":
        _check_zip(data, settings)
        result = Extracted(_docx(data), kind)
    elif kind == "xlsx":
        _check_zip(data, settings)
        result = Extracted(_xlsx(data), kind)
    elif kind == "html":
        result = Extracted(html_to_text(_decode(data)), kind)
    else:
        result = Extracted(_decode(data), kind)
    result.text = tidy(result.text)
    return result


def _decode(data: bytes) -> str:
    if b"\x00" in data:
        raise UnsupportedTypeError("binary data declared as text")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1250", errors="replace")  # legacy Hungarian Windows encoding


def _ocr(images: list[Image.Image], engine: OcrEngine, settings: Settings, dpi: int) -> list[str]:
    texts = engine.ocr(images, dpi)
    return [fix_hungarian_accents(t) if settings.fix_hu_accents else t for t in texts]


# -- PDF ------------------------------------------------------------------------
def _pdf(data: bytes, engine: OcrEngine, settings: Settings) -> Extracted:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise ExtractionError("PDF cannot be opened (damaged or password-protected)") from exc
    try:
        n = len(pdf)
        if n > settings.max_pages:
            raise TooLargeError(f"PDF has {n} pages; the limit is {settings.max_pages}")
        texts: list[str] = []
        ocr_pages = 0
        for i in range(n):
            page = pdf[i]
            try:
                textpage = page.get_textpage()
                layer = textpage.get_text_bounded()
                textpage.close()
                if sum(not c.isspace() for c in layer) >= settings.min_text_layer_chars:
                    texts.append(layer)
                    continue
                width, height = page.get_size()  # points (1/72 inch)
                scale = settings.pdf_dpi / 72
                if width * height * scale * scale > settings.max_pixels:
                    scale = math.sqrt(settings.max_pixels / (width * height))
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                bitmap.close()
            finally:
                page.close()
            texts.append(_ocr([image], engine, settings, round(scale * 72))[0])
            ocr_pages += 1
        return Extracted("\n\n".join(texts), "pdf", pages=n, ocr_pages=ocr_pages)
    finally:
        pdf.close()


# -- images -----------------------------------------------------------------------
def _image(data: bytes, engine: OcrEngine, settings: Settings) -> Extracted:
    try:
        image = Image.open(io.BytesIO(data), formats=IMAGE_FORMATS)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ExtractionError("image cannot be decoded") from exc
    if image.width * image.height > settings.max_pixels:
        raise TooLargeError(f"image has {image.width}x{image.height} pixels; the limit is {settings.max_pixels}")
    dpi = _dpi(image.info.get("dpi"))
    texts: list[str] = []
    # One frame at a time: a multi-page TIFF or GIF is never held in memory
    # as a whole.
    try:
        frames = getattr(image, "n_frames", 1)
    except (OSError, ValueError) as exc:
        raise ExtractionError("image cannot be decoded") from exc
    if frames > settings.max_pages:
        raise TooLargeError(f"image has {frames} frames; the limit is {settings.max_pages}")
    for i in range(frames):
        try:
            image.seek(i)
        except (EOFError, OSError, ValueError) as exc:
            raise ExtractionError("image cannot be decoded") from exc
        if image.width * image.height > settings.max_pixels:
            raise TooLargeError(f"image frame exceeds {settings.max_pixels} pixels")
        try:
            frame = ImageOps.exif_transpose(image.convert("RGB"))
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ExtractionError("image cannot be decoded") from exc
        texts += _ocr([frame], engine, settings, dpi)
        del frame
    return Extracted("\n\n".join(texts), "image", pages=len(texts), ocr_pages=len(texts))


def _dpi(value) -> int:
    """Resolution from image metadata, clamped; 300 when absent or malformed."""
    try:
        dpi = float(value[0] if isinstance(value, (tuple, list)) else value)
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return 300
    return int(min(max(dpi, 70), 1200)) if dpi == dpi and dpi > 0 else 300


# -- Office -------------------------------------------------------------------------
def _check_zip(data: bytes, settings: Settings) -> None:
    """Refuse ZIP bombs before a parser inflates them."""
    try:
        infos = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except zipfile.BadZipFile as exc:
        raise ExtractionError("damaged ZIP container") from exc
    if sum(i.file_size for i in infos) > settings.max_unzipped_bytes:
        raise TooLargeError("document expands beyond the size limit")


def _docx(data: bytes) -> str:
    import docx
    from docx.table import Table

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - python-docx raises various types
        raise ExtractionError("DOCX cannot be read") from exc
    parts: list[str] = []
    for section in document.sections:
        parts += [p.text for p in section.header.paragraphs if p.text.strip()]
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            parts.append(_docx_table(block))
        else:
            parts.append(block.text)
    for section in document.sections:
        parts += [p.text for p in section.footer.paragraphs if p.text.strip()]
    return "\n".join(parts)


def _docx_table(table) -> str:
    rows = []
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:
            if id(cell._tc) in seen:  # merged cells are repeated by python-docx
                continue
            seen.add(id(cell._tc))
            cells.append(cell.text.replace("\n", " "))
        rows.append("\t".join(cells))
    return "\n".join(rows)


def _xlsx(data: bytes) -> str:
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("XLSX cannot be read") from exc
    parts: list[str] = []
    cells = 0
    try:
        for sheet in workbook.worksheets:
            parts.append(f"# {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells += max(len(row), 1)
                if cells > MAX_SPREADSHEET_CELLS:
                    raise TooLargeError(f"spreadsheet has more than {MAX_SPREADSHEET_CELLS} cells")
                values = ["" if v is None else str(v) for v in row]
                if any(values):
                    parts.append("\t".join(values).rstrip("\t"))
    finally:
        workbook.close()
    return "\n".join(parts)
