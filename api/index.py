"""gdoc2html Vercel serverless entry point.

Deploy: vercel.json points here as the backend.
No Playwright, no filesystem — everything runs in-memory per request.
"""

import os
import io
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from bs4 import BeautifulSoup

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.fetch import fetch_full
from core.clean import clean_soup, classify_images, inject_responsive_css
from core.drive import list_drive_files, match_by_index, drive_id_to_lh3

app = FastAPI(title="gdoc2html", version="1.0")


# ── Request models ──────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    url: str
    drive_url: str | None = None
    gapi_key: str | None = None  # optional Google API key for Drive access


# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent.parent / "public" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/api/run")
async def run(req: RunRequest):
    """End-to-end: fetch Google Doc → clean HTML → optional Drive image rehost."""
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Thiếu URL")

    log: list[str] = [f"[run] {url}"]

    # 1. Fetch doc
    try:
        fetched = fetch_full(url)
    except Exception as e:
        raise HTTPException(400, f"Không fetch được doc: {e}")

    doc_id = fetched["doc_id"]
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
    log.append(f"  {len(keep)} ảnh lh3 (giữ), {len(replace_imgs)} cần re-host")

    # 3. Suggested filenames (for user reference)
    from core.utils import short_doc_id, build_suggested_names
    short_id = short_doc_id(doc_id)
    suggested = build_suggested_names(short_id, len(replace_imgs))

    # 4. Drive rehost (if URL provided)
    replaced = 0
    drive_files_count = 0
    if req.drive_url and replace_imgs:
        try:
            gapi_key = req.gapi_key or os.environ.get("GAPI_KEY") or ""
            drive_files = list_drive_files(req.drive_url, api_key=gapi_key)
            drive_files_count = len(drive_files)
            log.append(f"  Drive folder có {drive_files_count} file")

            mapping = match_by_index(drive_files, short_id, len(replace_imgs))

            # Apply mapping
            for idx, img in enumerate(replace_imgs, 1):
                if idx in mapping:
                    img["src"] = mapping[idx]
                    replaced += 1

            log.append(f"  matched {replaced}/{len(replace_imgs)} ảnh")
        except Exception as e:
            log.append(f"  ⚠ Drive error: {e}")
    elif replace_imgs:
        log.append("  không có drive_url — ảnh giữ nguyên URL Google gốc")
    else:
        log.append("  không có ảnh cần re-host")

    # 5. Inject responsive CSS + serialize HTML
    inject_responsive_css(soup)
    out_html = str(soup)
    out_size = len(out_html.encode("utf-8"))

    # 6. Download images inline (base64) for offline viewing
    #    Only for Google-hosted images that were already kept (lh3)
    #    For re-hosted ones, the lh3 URLs will work cross-origin

    return {
        "ok": True,
        "doc_id": doc_id,
        "short_id": short_id,
        "imgs_total": len(replace_imgs),
        "imgs_kept": len(keep),
        "imgs_replaced": replaced,
        "drive_files_count": drive_files_count,
        "suggested_names": suggested,
        "out_size": out_size,
        "html": out_html,
        "log": log,
    }


@app.get("/api/health")
async def health():
    return {"ok": True, "version": "1.0"}
