"""gdoc2html Vercel serverless entry point — base64 image inline.

Flow:
  1. POST /api/fetch → fetch doc → download all images → base64 inline → return complete HTML
  2. GET  /api/health → health check
  3. GET  /            → serve frontend

No external storage, no Drive API, no API keys needed.
Output HTML is self-contained — images embedded as data URIs.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from bs4 import BeautifulSoup

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.fetch import fetch_full
from core.clean import clean_soup, classify_images, inject_responsive_css, inline_images_base64
from core.utils import short_doc_id

app = FastAPI(title="gdoc2html", version="1.0")

# ── CORS ────────────────────────────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ──────────────────────────────────────────────────────────

class FetchRequest(BaseModel):
    url: str


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent.parent / "public" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health():
    return {"ok": True, "version": "1.0"}


@app.post("/api/fetch")
async def fetch_doc(req: FetchRequest):
    """Fetch Google Doc → clean → download images → base64 inline → return HTML.

    This is the only endpoint you need. The returned HTML is self-contained:
    all images embedded as base64 data URIs. Works offline, no external deps.
    """
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Thiếu URL")

    log: list[str] = [f"[fetch] {url}"]

    # 1. Fetch doc HTML
    try:
        fetched = fetch_full(url)
    except Exception as e:
        raise HTTPException(400, f"Không fetch được doc: {e}")

    doc_id = fetched["doc_id"]
    short_id = short_doc_id(doc_id)
    log.append(f"  doc_id={doc_id} mode={fetched['mode']}")

    # 2. Parse + clean
    soup = BeautifulSoup(
        f"<html><head></head><body>{fetched['html']}</body></html>",
        "html.parser",
    )
    for s in soup.find_all(["script", "noscript"]):
        s.decompose()
    clean_soup(soup)

    keep, replace_imgs = classify_images(soup)
    log.append(f"  {len(keep)} ảnh lh3 (giữ), {len(replace_imgs)} ảnh cần inline")

    # 3. Base64 inline all non-lh3 images
    inline_result = inline_images_base64(replace_imgs)
    log.append(f"  inline: {inline_result['inlined']} ok, {inline_result['failed']} fail, "
               f"~{inline_result['size_bytes'] / 1024:.0f} KB ảnh embedded")

    if inline_result["errors"]:
        for err in inline_result["errors"][:5]:
            log.append(f"  ⚠ {err}")

    # 4. Inject responsive CSS
    inject_responsive_css(soup)
    out_html = str(soup)
    out_size = len(out_html.encode("utf-8"))

    log.append(f"  output: ~{out_size / 1024:.0f} KB")

    # Build image manifest for display
    images = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("data:"):
            prefix = "🟢 base64 inline"
        elif any(h in urlparse(src).netloc for h in ("lh3.googleusercontent.com",)):
            prefix = "🔗 lh3 (keep)"
        else:
            prefix = src[:60]
        images.append(prefix)

    return {
        "ok": True,
        "doc_id": doc_id,
        "short_id": short_id,
        "imgs_total": len(replace_imgs),
        "imgs_inlined": inline_result["inlined"],
        "imgs_failed": inline_result["failed"],
        "imgs_kept_lh3": len(keep),
        "out_size": out_size,
        "out_size_kb": round(out_size / 1024, 1),
        "html": out_html,
        "log": log,
    }
