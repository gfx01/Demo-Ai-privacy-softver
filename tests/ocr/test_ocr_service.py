"""OCR service: file-type detection, extraction, limits and the HTTP contract.

Tesseract tests need the language models (``make test-ocr`` downloads them
to models/tessdata) and a TrueType font with Hungarian letters to render
test images; they are skipped otherwise.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("tesserocr")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from ocr_service import app as app_module  # noqa: E402
from ocr_service.config import ConfigError, Settings  # noqa: E402
from ocr_service.engines import TesseractEngine, page_text  # noqa: E402
from ocr_service.extract import (  # noqa: E402
    ExtractionError,
    TooLargeError,
    UnsupportedTypeError,
    extract,
    sniff,
)
from ocr_service.text import fix_hungarian_accents, html_to_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TESSDATA = Path(os.environ.get("OCR_TESSDATA_DIR", ROOT / "models" / "tessdata"))
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
KEY = "test-ocr-key-0123456789"
LINES = ["Tisztelt Kőműves Ödön Úr!", "Szőke Tünde, 1117 Budapest, Bartók Béla út 12."]


class FakeEngine:
    name = "fake"

    def __init__(self, text: str = "OCR SZÖVEG") -> None:
        self.text = text
        self.calls: list[tuple[int, int]] = []

    def ocr(self, images, dpi):
        self.calls.append((len(images), dpi))
        return [self.text for _ in images]


def settings(**kw) -> Settings:
    return Settings(api_key=KEY, **kw)


def render(lines=LINES, size=28) -> Image.Image:
    if not FONT.exists():
        pytest.skip("DejaVuSans font not installed")
    font = ImageFont.truetype(str(FONT), size)
    img = Image.new("L", (1300, 60 * len(lines) + 40), 255)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((30, 30 + 60 * i), line, fill=0, font=font)
    return img


def to_bytes(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


@pytest.fixture(scope="module")
def tesseract():
    if not (TESSDATA / "hun.traineddata").exists():
        pytest.skip("run: make test-ocr (downloads models/tessdata)")
    return TesseractEngine(str(TESSDATA), "hun+eng")


def text_pdf(text: str) -> bytes:
    """Minimal one-page PDF with a text layer (standard Helvetica font)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


# -- detection -------------------------------------------------------------------
def test_sniff_by_magic_bytes_not_by_declared_type():
    img = Image.new("RGB", (10, 10), "white")
    assert sniff(to_bytes(img, "PNG"), "text/plain", "a.txt") == "image"
    assert sniff(to_bytes(img, "JPEG"), "", "") == "image"
    assert sniff(text_pdf("x"), "application/octet-stream", "x.bin") == "pdf"
    assert sniff(b"BMW es Audi", "text/plain", "autok.txt") == "text"
    assert sniff(to_bytes(img, "BMP"), "image/bmp", "a.bmp") == "image"
    assert sniff(b"<p>hello</p>", "text/html", "") == "html"
    assert sniff(b"a;b\n1;2", "", "adat.csv") == "text"


def test_unsupported_types_are_refused():
    with pytest.raises(UnsupportedTypeError):
        sniff(b"\x00\x01binary", "application/octet-stream", "x.bin")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/presentation.xml", "<p/>")
    with pytest.raises(UnsupportedTypeError):
        sniff(buf.getvalue(), "", "x.pptx")
    with pytest.raises(UnsupportedTypeError):
        extract(b"abc\x00def", "text/plain", "x.txt", FakeEngine(), settings())


def test_text_decoding_utf8_and_cp1250():
    assert extract("Árvíztűrő\r\n".encode(), "text/plain", "", FakeEngine(), settings()).text == "Árvíztűrő"
    assert extract("Kőműves Ödön".encode("cp1250"), "text/plain", "", FakeEngine(), settings()).text == "Kőműves Ödön"


def test_html_to_text_and_accent_fix():
    html = "<html><head><title>T</title><script>x=1</script></head><body><p>Kovács&nbsp;Péter</p><table><tr><td>a</td><td>b</td></tr></table></body></html>"
    assert html_to_text(html) == "Kovács\xa0Péter\n\n\ta\tb"
    assert fix_hungarian_accents("Kõmûves ÕRÛ") == "Kőműves ŐRŰ"


# -- PDF ----------------------------------------------------------------------------
def test_pdf_text_layer_is_used_without_ocr():
    engine = FakeEngine()
    result = extract(text_pdf("Kovacs Peter szerzodese"), "application/pdf", "a.pdf", engine, settings())
    assert result.text == "Kovacs Peter szerzodese"
    assert (result.kind, result.pages, result.ocr_pages) == ("pdf", 1, 0)
    assert engine.calls == []


def test_scanned_pdf_pages_are_rendered_and_ocrd():
    engine = FakeEngine("lapolvasás")
    pages = [Image.new("RGB", (400, 300), "white") for _ in range(3)]
    pdf = to_bytes(pages[0], "PDF", save_all=True, append_images=pages[1:])
    result = extract(pdf, "application/pdf", "scan.pdf", engine, settings(pdf_dpi=150))
    assert (result.pages, result.ocr_pages) == (3, 3)
    assert result.text == "lapolvasás\n\nlapolvasás\n\nlapolvasás"
    assert engine.calls == [(1, 150)] * 3


def test_pdf_page_limit_and_damaged_pdf():
    pages = [Image.new("RGB", (50, 50), "white") for _ in range(3)]
    pdf = to_bytes(pages[0], "PDF", save_all=True, append_images=pages[1:])
    with pytest.raises(TooLargeError):
        extract(pdf, "application/pdf", "", FakeEngine(), settings(max_pages=2))
    with pytest.raises(ExtractionError):
        extract(b"%PDF-1.4\ngarbage", "application/pdf", "", FakeEngine(), settings())


# -- images ---------------------------------------------------------------------------
def test_image_limits_and_frames():
    engine = FakeEngine()
    frames = [Image.new("L", (100, 100), 255) for _ in range(3)]
    tiff = to_bytes(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    assert extract(tiff, "image/tiff", "", engine, settings()).ocr_pages == 3
    with pytest.raises(TooLargeError):
        extract(tiff, "image/tiff", "", engine, settings(max_pages=2))
    with pytest.raises(TooLargeError):
        extract(to_bytes(Image.new("L", (300, 300), 255), "PNG"), "image/png", "", engine, settings(max_pixels=50_000))
    with pytest.raises(ExtractionError):
        extract(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "image/png", "", engine, settings())


def test_engine_gets_decoded_pixels_and_accents_are_fixed():
    engine = FakeEngine("Kõmûves")
    out = extract(to_bytes(Image.new("RGB", (64, 64), "white"), "WEBP"), "image/webp", "", engine, settings())
    assert out.text == "Kőműves"
    out = extract(to_bytes(Image.new("RGB", (64, 64), "white"), "WEBP"), "image/webp", "", engine, settings(fix_hu_accents=False))
    assert out.text == "Kõmûves"


def test_tesseract_reads_hungarian_letters(tesseract):
    out = extract(to_bytes(render(), "PNG"), "image/png", "level.png", tesseract, settings())
    assert "Kőműves Ödön" in out.text
    assert "Szőke Tünde" in out.text
    assert "Bartók Béla út 12" in out.text


def test_tesseract_on_scanned_pdf(tesseract):
    pdf = to_bytes(render().convert("RGB"), "PDF", resolution=150)
    out = extract(pdf, "application/pdf", "scan.pdf", tesseract, settings())
    assert out.ocr_pages == 1
    assert "Kőműves Ödön" in out.text


# -- Office -----------------------------------------------------------------------------
def test_docx_paragraphs_tables_header_in_order():
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.sections[0].header.paragraphs[0].text = "Iktatószám: 12/2026"
    document.add_paragraph("Megbízó: Kovács Péter")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Név", "Telefon"
    table.cell(1, 0).text, table.cell(1, 1).text = "Nagy Anna", "+36 30 000 1234"
    document.add_paragraph("Kelt: Szeged")
    buf = io.BytesIO()
    document.save(buf)
    out = extract(buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx", FakeEngine(), settings())
    assert out.kind == "docx"
    assert out.text == "Iktatószám: 12/2026\nMegbízó: Kovács Péter\nNév\tTelefon\nNagy Anna\t+36 30 000 1234\nKelt: Szeged"


def test_xlsx_sheets_and_rows():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ügyfelek"
    ws.append(["Név", "Város"])
    ws.append(["Tóth Ilona", "Pécs"])
    ws.append([None, None])
    buf = io.BytesIO()
    wb.save(buf)
    out = extract(buf.getvalue(), "", "lista.xlsx", FakeEngine(), settings())
    assert out.text == "# Ügyfelek\nNév\tVáros\nTóth Ilona\tPécs"


def test_zip_bomb_is_refused_before_parsing():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document/>")
        z.writestr("word/media/big.bin", b"\x00" * 5_000_000)
    with pytest.raises(TooLargeError):
        extract(buf.getvalue(), "", "a.docx", FakeEngine(), settings(max_unzipped_bytes=1_000_000))


# -- Surya result parsing (engine itself not runnable here) ------------------------------
def test_surya_page_text_reading_order_and_skips():
    block = lambda order, html, skipped=False, error=False: SimpleNamespace(  # noqa: E731
        reading_order=order, html=html, skipped=skipped, error=error
    )
    page = SimpleNamespace(blocks=[
        block(2, "<p>Második <b>bekezdés</b></p>"),
        block(0, "<h1>Cím</h1>"),
        block(1, "", skipped=True),
        block(3, "<p>hibás</p>", error=True),
        block(4, "<table><tr><td>Név</td><td>Kis Éva</td></tr></table>"),
    ])
    assert page_text(page) == "Cím\n\nMásodik bekezdés\n\nNév\tKis Éva"


# -- configuration -------------------------------------------------------------------------
def test_surya_requires_licence_acknowledgement_and_server():
    with pytest.raises(ConfigError, match="OCR_SURYA_LICENSE_ACCEPTED"):
        settings(engine="surya", surya_inference_url="http://surya-vlm:8000/v1").validate()
    with pytest.raises(ConfigError, match="SURYA_INFERENCE_URL"):
        settings(engine="surya", surya_license_accepted=True).validate()
    settings(engine="surya", surya_license_accepted=True, surya_inference_url="http://surya-vlm:8000/v1").validate()
    with pytest.raises(ConfigError, match="OCR_API_KEY"):
        Settings(api_key="short").validate()
    with pytest.raises(ConfigError, match="OCR_ENGINE"):
        settings(engine="cloud").validate()


# -- HTTP contract -------------------------------------------------------------------------
@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OCR_API_KEY", KEY)
    monkeypatch.setenv("OCR_MAX_BYTES", "200000")
    engine = FakeEngine("Kép szövege")
    monkeypatch.setattr(app_module, "make_engine", lambda s: engine)
    with TestClient(app_module.app) as c:
        yield c


def test_process_contract(client):
    png = to_bytes(Image.new("RGB", (32, 32), "white"), "PNG")
    r = client.put("/process", content=png, headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "image/png",
        "X-Filename": "abc_Kov%C3%A1cs%20P%C3%A9ter.png", "X-OpenWebUI-User-Email": "x@example.com",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["page_content"] == "Kép szövege"
    assert body["metadata"] == {"source": "privacy-hub-ocr", "engine": "fake", "kind": "image", "pages": 1, "ocr_pages": 1}
    assert "Kov" not in r.text  # the file name is not echoed back


def test_process_errors(client):
    auth = {"Authorization": f"Bearer {KEY}"}
    assert client.put("/process", content=b"x", headers={"Content-Type": "text/plain"}).status_code == 401
    assert client.put("/process", content=b"x", headers={"Authorization": "Bearer wrong-key-000000000"}).status_code == 401
    assert client.put("/process", content=b"", headers=auth).status_code == 422
    assert client.put("/process", content=b"x" * 200_001, headers={**auth, "Content-Type": "text/plain"}).status_code == 413
    assert client.put("/process", content=b"\x00\x01", headers={**auth, "Content-Type": "application/octet-stream"}).status_code == 415
    r = client.put("/process", content="Szia Péter".encode(), headers={**auth, "Content-Type": "text/plain"})
    assert r.status_code == 200 and r.json()["page_content"] == "Szia Péter"
    assert r.json()["metadata"]["engine"] == "none"
    assert client.get("/health").json() == {"status": "ok", "engine": "fake"}
