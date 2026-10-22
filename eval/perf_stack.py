"""End-to-end latency and memory of the running stack (mock provider).

    .venv-gateway/bin/python eval/perf_stack.py

Measures, through nginx and the guardrail, with the mock model (so the time
is the privacy hub's own overhead plus a near-instant fake LLM):
* text requests: a typical Hungarian prompt (~450 characters, 6 entities)
* image requests: a rendered Hungarian letter (PNG, 200 dpi), each one new so
  the gateway's OCR cache is not hit
* 16 text requests from 8 threads, to see queueing (within nginx's burst)
and the memory of every container (one `docker stats` sample).
Writes eval/out/perf_stack.json.
"""

from __future__ import annotations

import base64
import io
import json
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "out"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
PROMPT = (
    "Kérlek, foglald össze röviden: Tisztelt Horváth Dóra! A Napsugár Kft. nevében értesítjük, hogy "
    "a 2026. március 3-án kelt szerződés szerinti 1 250 000 Ft összeget Kele Ákos ügyvezető aláírása után "
    "átutaljuk. Kérdés esetén hívja a +36 30 000 4417 számot, vagy írjon a horvath.dora@napsugar.example "
    "címre. Szállítási cím: 6720 Szeged, Kárász utca 12. Üdvözlettel: Szőkéné Bárány Ilona"
)


def env() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


def image_url(n: int) -> str:
    font = ImageFont.truetype(FONT, 31)  # 11 pt at 200 dpi
    lines = [f"Iktatószám: {n:05d}/2026", "Tisztelt Bárány Ilona!", "Értesítjük, hogy a kérelmét Kele Ákos ügyintéző",
             "befogadta. Telefon: +36 30 000 4417, e-mail: kele.akos@hivatal.example", "6720 Szeged, Kárász utca 12."]
    img = Image.new("L", (1400, 80 + 60 * len(lines)), 255)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((40, 40 + 60 * i), line, fill=0, font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG", dpi=(200, 200))
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def timed(client: httpx.Client, key: str, content, pace_s: float = 0.0) -> tuple[float, int]:
    """One request; pace_s keeps sequential runs under nginx's rate limit
    (10 requests/s per client IP, burst 20)."""
    body = {"model": "mock-gpt", "messages": [{"role": "user", "content": content}]}
    t = time.perf_counter()
    r = client.post("/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    elapsed = time.perf_counter() - t
    time.sleep(max(0.0, pace_s - elapsed))
    return elapsed * 1000, r.status_code


def stats(values: list[float]) -> dict:
    values = sorted(values)
    return {"n": len(values), "p50_ms": round(statistics.median(values), 1),
            "p95_ms": round(values[int(0.95 * (len(values) - 1))], 1), "max_ms": round(values[-1], 1)}


def main() -> int:
    e = env()
    key = e["OPENWEBUI_LITELLM_KEY"]
    base = f"http://127.0.0.1:{e.get('GATEWAY_PORT', '8080')}"
    result: dict = {}
    with httpx.Client(base_url=base, timeout=300) as client:
        for _ in range(3):
            timed(client, key, PROMPT, 0.15)  # warm-up
        text = [timed(client, key, PROMPT, 0.15) for _ in range(40)]
        assert all(s == 200 for _, s in text), text
        result["text_request"] = stats([t for t, _ in text])
        images = []
        for n in range(12):
            content = [{"type": "text", "text": "Mi áll a levélben?"}, {"type": "image_url", "image_url": {"url": image_url(n)}}]
            images.append(timed(client, key, content, 0.15))
        assert all(s == 200 for _, s in images), images
        result["image_request_ocr"] = stats([t for t, _ in images])
        time.sleep(3)  # refill nginx's burst allowance
        with ThreadPoolExecutor(8) as pool:
            t0 = time.perf_counter()
            par = list(pool.map(lambda _: timed(client, key, PROMPT), range(16)))
            wall = time.perf_counter() - t0
        assert all(s == 200 for _, s in par), par
        result["text_8_parallel"] = {**stats([t for t, _ in par]), "requests_per_s": round(16 / wall, 1)}
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"],
                         capture_output=True, text=True, check=True).stdout
    result["memory"] = dict(line.split("\t", 1) for line in out.strip().splitlines() if line.startswith("privacy-hub-"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "perf_stack.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
