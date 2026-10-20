"""Documents and images against the running stack: OCR, masking, isolation.

Test images are rendered with DejaVu Sans (Hungarian letters); the tests are
skipped where that font or Pillow is missing.
"""

from __future__ import annotations

import base64
import io
import json
import uuid
from pathlib import Path

import pytest

from .conftest import compose, wait_healthy

pytestmark = pytest.mark.integration

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
LINES = ["Tisztelt Kovács Péter!", "E-mail: kovacs.peter@example.com", "Telefon: +36 30 000 1234"]
QUESTION = "Kinek szól a levél? Válaszolj röviden."


def render(lines=LINES) -> Image.Image:
    if not FONT.exists():
        pytest.skip("DejaVuSans font not installed")
    font = ImageFont.truetype(str(FONT), 30)
    img = Image.new("RGB", (1100, 70 * len(lines) + 40), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((30, 30 + 70 * i), line, fill="black", font=font)
    return img


def data_url(img: Image.Image, fmt: str = "PNG", mime: str = "image/png") -> str:
    buf = io.BytesIO()
    img.save(buf, fmt)
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def ask(gateway, key, *parts, model="mock-gpt"):
    body = {"model": model, "messages": [{"role": "user", "content": [{"type": "text", "text": QUESTION}, *parts]}]}
    r = gateway.post("/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    return r.status_code, r.json()


def sent_text(mock_provider) -> str:
    received = mock_provider.received()
    assert len(received) == 1
    return json.dumps(received[0]["messages"], ensure_ascii=False)


def test_chat_image_is_ocrd_and_masked_before_the_provider(gateway, user_key, mock_provider):
    status, body = ask(gateway, user_key, {"type": "image_url", "image_url": {"url": data_url(render())}})
    assert status == 200, body
    sent = sent_text(mock_provider)
    assert "image_url" not in sent and "base64" not in sent
    assert "[Csatolt kép szövege (OCR)]" in sent
    for secret in ("Kovács", "kovacs.peter", "000 1234"):
        assert secret not in sent, sent
    assert "[PERSON_1]" in sent and "[EMAIL_1]" in sent
    # the mock echoes the prompt: the answer comes back restored
    assert "Kovács Péter" in body["choices"][0]["message"]["content"]


def test_chat_pdf_file_part_and_file_name_are_masked(gateway, user_key, mock_provider):
    buf = io.BytesIO()
    render().save(buf, "PDF", resolution=150)
    part = {"type": "file", "file": {"filename": "Kovács Péter levele.pdf",
                                     "file_data": "data:application/pdf;base64," + base64.b64encode(buf.getvalue()).decode()}}
    status, body = ask(gateway, user_key, part)
    assert status == 200, body
    sent = sent_text(mock_provider)
    assert "Kovács" not in sent and "kovacs.peter" not in sent
    assert "levele.pdf" in sent and "[Csatolt fájl szövege:" in sent


def test_remote_image_url_is_refused(gateway, user_key, mock_provider):
    status, body = ask(gateway, user_key, {"type": "image_url", "image_url": {"url": "https://example.com/scan.png"}})
    assert status == 400 and "privacy_hub_unsupported_content" in json.dumps(body)
    assert mock_provider.received() == []


def test_ocr_down_blocks_attachments_but_not_text(gateway, user_key, mock_provider):
    # a new image: one already seen is answered from the gateway's short-lived OCR cache
    image = data_url(render(LINES + [f"Iktatószám: {uuid.uuid4().hex[:8]}"]))
    compose("stop", "ocr")
    try:
        status, body = ask(gateway, user_key, {"type": "image_url", "image_url": {"url": image}})
        assert status == 503 and "privacy_hub_unavailable" in json.dumps(body)
        assert mock_provider.received() == [], "provider must not be called when the image cannot be read"
        r = gateway.post(
            "/v1/chat/completions",
            json={"model": "mock-gpt", "messages": [{"role": "user", "content": "Írj Kovács Péternek."}]},
            headers={"Authorization": f"Bearer {user_key}"},
        )
        assert r.status_code == 200
    finally:
        compose("start", "ocr")
        wait_healthy("ocr")


OCR_PROBE = (
    "import json,os,sys,urllib.request,urllib.error;"
    "r=urllib.request.Request('http://ocr:8000/process',data=b'Szia',method='PUT',"
    "headers={'Content-Type':'text/plain','Authorization':'Bearer '+sys.argv[1]});"
    "\ntry: print(urllib.request.urlopen(r, timeout=10).status)"
    "\nexcept urllib.error.HTTPError as e: print(e.code)"
)


def test_ocr_service_requires_its_key_and_is_not_published(env):
    out = compose("exec", "-T", "litellm", "python3", "-c", OCR_PROBE, "wrong-key-0000000000").stdout.strip()
    assert out == "401"
    out = compose("exec", "-T", "litellm", "python3", "-c", OCR_PROBE, env["OCR_API_KEY"]).stdout.strip()
    assert out == "200"
    published = compose("port", "ocr", "8000", check=False)
    assert published.returncode != 0 or published.stdout.strip() in ("", ":0"), published.stdout


def test_ocr_service_has_no_internet_access():
    probe = (
        "import urllib.request\n"
        "try:\n urllib.request.urlopen('https://pypi.org/simple/', timeout=5); print('reachable')\n"
        "except Exception as e: print('blocked', type(e).__name__)"
    )
    out = compose("exec", "-T", "ocr", "python", "-c", probe).stdout
    assert out.startswith("blocked"), out


def test_openwebui_extracts_uploads_through_the_ocr_service():
    """Runs Open WebUI's own external-loader code inside its container."""
    buf = io.BytesIO()
    render().save(buf, "PNG")
    script = (
        "import base64,os,sys,tempfile\n"
        "from open_webui.retrieval.loaders.external_document import ExternalDocumentLoader\n"
        "assert os.environ['CONTENT_EXTRACTION_ENGINE'] == 'external'\n"
        "p = os.path.join(tempfile.mkdtemp(), 'upload_level.png')\n"
        "open(p, 'wb').write(base64.b64decode(sys.argv[1]))\n"
        "docs = ExternalDocumentLoader(p, os.environ['EXTERNAL_DOCUMENT_LOADER_URL'],"
        " os.environ['EXTERNAL_DOCUMENT_LOADER_API_KEY'], mime_type='image/png').load()\n"
        "print(docs[0].page_content)\n"
    )
    out = compose("exec", "-T", "openwebui", "python", "-c", script, base64.b64encode(buf.getvalue()).decode())
    assert "Kovács Péter" in out.stdout, out.stderr[-2000:]


def test_local_only_config_inside_the_gateway_image():
    """The entrypoint's local-only filter and the startup check, run in the
    real image: external provider entries are dropped, and the unfiltered
    config is refused."""
    script = (
        "set -e; export PRIVACY_HUB_LOCAL_ONLY=true; "
        "python3 -m privacy_hub.local_only /app/privacy/config.yaml /tmp/lo.yaml; "
        "python3 -m privacy_hub.startup_check /tmp/lo.yaml; "
        "if python3 -m privacy_hub.startup_check /app/privacy/config.yaml; then exit 9; fi"
    )
    out = compose("exec", "-T", "litellm", "sh", "-c", script, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "removed external model entries: openai/*, anthropic/*, gemini/*" in out.stdout
    assert "model 'openai/*' would send requests to the provider's public API" in out.stderr
