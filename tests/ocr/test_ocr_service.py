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


# -- HTTP contract (the service with its worker process and a fake engine) --------------
PNG = None


def png() -> bytes:
    return to_bytes(Image.new("RGB", (32, 32), "white"), "PNG")


@pytest.fixture()
def make_client(monkeypatch):
    from fastapi.testclient import TestClient

    clients = []

    def make(factory="ok", **env):
        monkeypatch.setenv("OCR_API_KEY", KEY)
        monkeypatch.setenv("OCR_MAX_BYTES", "200000")
        monkeypatch.setenv("OCR_ENGINE_FACTORY", f"tests.ocr.fakes:{factory}")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        c = TestClient(app_module.app)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


AUTH = {"Authorization": f"Bearer {KEY}"}


def test_process_contract(make_client):
    client = make_client()
    r = client.put("/process", content=png(), headers={
        **AUTH, "Content-Type": "image/png",
        "X-Filename": "abc_Kov%C3%A1cs%20P%C3%A9ter.png", "X-OpenWebUI-User-Email": "x@example.com",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["page_content"] == "Kép szövege"
    assert body["metadata"] == {"extractor": "privacy-hub-ocr", "engine": "fake", "kind": "image", "pages": 1, "ocr_pages": 1}
    assert "Kov" not in r.text  # the file name is not echoed back


def test_process_errors(make_client):
    client = make_client()
    assert client.put("/process", content=b"x", headers={"Content-Type": "text/plain"}).status_code == 401
    assert client.put("/process", content=b"x", headers={"Authorization": "Bearer wrong-key-000000000"}).status_code == 401
    assert client.put("/process", content=b"", headers=AUTH).status_code == 422
    assert client.put("/process", content=b"x" * 200_001, headers={**AUTH, "Content-Type": "text/plain"}).status_code == 413
    assert client.put("/process", content=b"\x00\x01", headers={**AUTH, "Content-Type": "application/octet-stream"}).status_code == 415
    r = client.put("/process", content="Szia Péter".encode(), headers={**AUTH, "Content-Type": "text/plain"})
    assert r.status_code == 200 and r.json()["page_content"] == "Szia Péter"
    assert r.json()["metadata"]["engine"] == "none"
    assert client.get("/health").json() == {"status": "ok", "engine": "fake"}


def test_timeout_kills_the_worker_and_the_service_recovers(make_client):
    import multiprocessing
    import time

    client = make_client("slow", OCR_TIMEOUT_S="2")
    started = time.monotonic()
    for _ in range(3):
        assert client.put("/process", content=png(), headers={**AUTH, "Content-Type": "image/png"}).status_code == 504
    assert time.monotonic() - started < 30
    # killed, not left running: only the current worker process is alive
    assert len(multiprocessing.active_children()) == 1
    r = client.put("/process", content="szöveg".encode(), headers={**AUTH, "Content-Type": "text/plain"})
    assert r.status_code == 200 and r.json()["page_content"] == "szöveg"


def test_worker_crash_is_contained(make_client):
    client = make_client("crash")
    r = client.put("/process", content=png(), headers={**AUTH, "Content-Type": "image/png"})
    assert r.status_code == 422
    r = client.put("/process", content="még működik".encode(), headers={**AUTH, "Content-Type": "text/plain"})
    assert r.status_code == 200 and r.json()["page_content"] == "még működik"


def test_engine_failure_is_503(make_client):
    client = make_client("fail")
    assert client.put("/process", content=png(), headers={**AUTH, "Content-Type": "image/png"}).status_code == 503


def test_service_refuses_to_start_when_the_engine_cannot_load(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OCR_API_KEY", KEY)
    monkeypatch.setenv("OCR_ENGINE_FACTORY", "tests.ocr.fakes:broken")
    with pytest.raises(RuntimeError, match="language data missing"):
        with TestClient(app_module.app):
            pass


# -- resource limits ---------------------------------------------------------------------------
def test_frame_limit_is_checked_before_any_ocr():
    engine = FakeEngine()
    frames = [Image.new("L", (64, 64), 255) for _ in range(4)]
    tiff = to_bytes(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    with pytest.raises(TooLargeError):
        extract(tiff, "image/tiff", "", engine, settings(max_pages=3))
    assert engine.calls == []


def test_xlsx_with_absurd_row_index_stops_quickly():
    """A 5 KB sheet whose only row has index 10^12 made openpyxl yield 10^12
    empty rows; empty rows now count against the cell limit."""
    import time

    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active["A1"] = "x"
    buf = io.BytesIO()
    wb.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1000000000000"><c r="A1000000000000" t="inlineStr"><is><t>x</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = sheet.encode() if item.filename == "xl/worksheets/sheet1.xml" else src.read(item.filename)
            dst.writestr(item, data)
    started = time.monotonic()
    with pytest.raises(TooLargeError):
        extract(out.getvalue(), "", "bomba.xlsx", FakeEngine(), settings())
    assert time.monotonic() - started < 20


# -- second review round ------------------------------------------------------------------------
def test_rtf_and_unlisted_text_types_are_refused():
    rtf = b"{\\rtf1\\ansi P\\'e9ter Kov\\'e1cs}"
    for ctype in ("text/rtf", "application/rtf", "text/plain"):
        with pytest.raises(UnsupportedTypeError):
            extract(rtf, ctype, "level.rtf", FakeEngine(), settings())
    with pytest.raises(UnsupportedTypeError):
        sniff(b"hello", "text/x-something", "x.unknown")
    assert sniff(b"print(1)", "", "script.py") == "text"
    assert sniff(b"<a>1</a>", "application/xml", "") == "text"


def test_tidy_is_linear_on_long_whitespace_runs():
    import time

    started = time.monotonic()
    out = extract(("a" + " " * 500_000 + "b").encode(), "text/plain", "", FakeEngine(), settings())
    assert out.text.startswith("a ") and out.text.endswith("b")
    assert time.monotonic() - started < 5


def test_text_budget_stops_expanding_documents():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    big = "x" * 10_000
    for r in range(1, 40):
        wb.active.cell(row=r, column=1, value=big)
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(TooLargeError):
        extract(buf.getvalue(), "", "a.xlsx", FakeEngine(), settings(max_text_chars=200_000))
    assert extract(buf.getvalue(), "", "a.xlsx", FakeEngine(), settings(max_text_chars=500_000)).kind == "xlsx"


def test_xlsx_wrong_dimension_does_not_hide_rows():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    for row in (["Név", "Város"], ["Tóth Ilona", "Pécs"], ["Kis Éva", "Győr"]):
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b'<dimension ref="A1:B3"/>', b'<dimension ref="A1"/>')
                assert b'<dimension ref="A1"/>' in data
            dst.writestr(item, data)
    text = extract(out.getvalue(), "", "a.xlsx", FakeEngine(), settings()).text
    assert "Kis Éva\tGyőr" in text


def _docx_with_xml(body_xml: str, footnote: str | None = None) -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    section = document.sections[0]
    section.different_first_page_header_footer = True
    section.first_page_header.paragraphs[0].text = "Első oldali fejléc: Dr. Szabó Anna"
    section.header.paragraphs[0].text = "Iktatószám: 7/2026"
    document.add_paragraph("PLACEHOLDER")
    buf = io.BytesIO()
    document.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "word/document.xml":
                start = data.index(b"<w:p>", data.index(b"<w:body>")) if b"<w:p>" in data else data.index(b"<w:p ")
                end = data.index(b"</w:p>", start) + len(b"</w:p>")
                data = data[:start] + body_xml.encode() + data[end:]
            if footnote and item.filename == "word/_rels/document.xml.rels":
                data = data.replace(b"</Relationships>", b'<Relationship Id="rIdFn" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/></Relationships>')
            if footnote and item.filename == "[Content_Types].xml":
                data = data.replace(b"</Types>", b'<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/></Types>')
            dst.writestr(item, data)
        if footnote:
            dst.writestr("word/footnotes.xml", (
                f'<w:footnotes {ns}><w:footnote w:id="-1"><w:p><w:r><w:t>SEPARATOR</w:t></w:r></w:p></w:footnote>'
                f'<w:footnote w:id="1"><w:p><w:r><w:t>{footnote}</w:t></w:r></w:p></w:footnote></w:footnotes>'
            ))
    return out.getvalue()


def test_docx_reads_controls_insertions_headers_and_footnotes_but_not_deletions():
    body = (
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>Ügyfél: Horváth Márk</w:t></w:r></w:p></w:sdtContent></w:sdt>"
        "<w:p><w:r><w:t xml:space=\"preserve\">Képviselő: </w:t></w:r><w:ins w:id=\"1\" w:author=\"x\"><w:r><w:t>Papp Rita</w:t></w:r></w:ins>"
        "<w:del w:id=\"2\" w:author=\"x\"><w:r><w:delText>Törölt Név</w:delText></w:r></w:del></w:p>"
        "<w:tbl><w:sdt><w:sdtContent><w:tr><w:tc><w:p><w:r><w:t>Varga Imre</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>Szeged</w:t></w:r></w:p></w:tc></w:tr></w:sdtContent></w:sdt></w:tbl>"
    )
    text = extract(_docx_with_xml(body, footnote="Lábjegyzet: Balogh Judit"), "", "a.docx", FakeEngine(), settings()).text
    for expected in ("Ügyfél: Horváth Márk", "Képviselő: Papp Rita", "Varga Imre\tSzeged",
                     "Első oldali fejléc: Dr. Szabó Anna", "Iktatószám: 7/2026", "Lábjegyzet: Balogh Judit"):
        assert expected in text, (expected, text)
    assert "Törölt Név" not in text and "SEPARATOR" not in text


def test_malformed_office_file_is_unreadable_not_engine_failure():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document><unclosed>")
    with pytest.raises(ExtractionError):
        extract(buf.getvalue(), "", "a.docx", FakeEngine(), settings())


def test_tiff_with_many_frames_is_refused_quickly():
    import time

    frames = [Image.new("1", (8, 8), 1) for _ in range(3000)]
    tiff = to_bytes(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    started = time.monotonic()
    with pytest.raises(TooLargeError):
        extract(tiff, "image/tiff", "", FakeEngine(), settings())
    assert time.monotonic() - started < 3


def test_queue_is_bounded(make_client):
    import threading

    client = make_client("slow", OCR_TIMEOUT_S="4", OCR_MAX_QUEUE="1")
    first = {}
    t = threading.Thread(target=lambda: first.setdefault("r", client.put("/process", content=png(), headers={**AUTH, "Content-Type": "image/png"})))
    t.start()
    import time

    time.sleep(1)
    r = client.put("/process", content=b"szia", headers={**AUTH, "Content-Type": "text/plain"})
    assert r.status_code == 503 and "busy" in r.text
    t.join()
    assert first["r"].status_code == 504
