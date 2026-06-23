"""gdoc2html Vercel serverless entry point.

Deploy: vercel.json points here as the backend.
No Playwright, no filesystem — everything runs in-memory per request.

Flow:
  1. POST /api/fetch  → fetch doc, clean, classify images, return HTML + image list
  2. GET  /api/proxy-image?url=... → download original image (sets correct referer)
  3. POST /api/render  → fetch doc + Drive rehost → return final HTML
  4. GET  /api/health  → health check
"""

import os
import re
import io
import zipfile
from pathlib import Path
from urllib.parse import urlparse, unquote

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel
from bs4 import BeautifulSoup

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.fetch import fetch_full, extract_doc_id
from core.clean import clean_soup, classify_images, inject_responsive_css
from core.drive import (
    list_drive_files, match_by_index, drive_id_to_lh3,
    build_suggested_names,
)
from core.utils import short_doc_id

app = FastAPI(title="gdoc2html", version="1.0")

# ── CORS (for local dev + Vercel) ──────────────────────────────────────────
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


class RenderRequest(BaseModel):
    url: str
    drive_url: str | None = None
    gapi_key: str | None = None


# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent.parent / "public" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health():
    return {"ok": True, "version": "1.0"}


@app.post("/api/fetch")
async def fetch_doc(req: FetchRequest):
    """Step 1: Fetch Google Doc → clean → classify images.

    Returns cleaned HTML + list of images needing re-host + suggested names.
    """
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Thiếu URL")

    log: list[str] = [f"[fetch] {url}"]

    try:
        fetched = fetch_full(url)
    except Exception as e:
        raise HTTPException(400, f"Không fetch được doc: {e}")

    doc_id = fetched["doc_id"]
    short_id = short_doc_id(doc_id)
    log.append(f"  doc_id={doc_id} mode={fetched['mode']}")

    # Parse + clean
    soup = BeautifulSoup(
        f"<html><head></head><body>{fetched['html']}</body></html>",
        "html.parser",
    )
    for s in soup.find_all(["script", "noscript"]):
        s.decompose()
    clean_soup(soup)

    keep, replace_imgs = classify_images(soup)
    log.append(f"  {len(keep)} ảnh lh3 (giữ), {len(replace_imgs)} cần re-host")

    # Build image list with original URLs + suggested filenames
    img_list = []
    for idx, img in enumerate(replace_imgs, 1):
        src = img.get("src", "")
        ext = "png"
        for e in ("png", "jpg", "jpeg", "webp", "gif", "svg"):
            if f".{e}" in src.lower().split("?")[0]:
                ext = e
                break
        suggested = f"{short_id}_{idx:03d}.{ext}"
        img_list.append({
            "idx": idx,
            "original_url": src,
            "suggested_name": suggested,
            "proxy_url": f"/api/proxy-image?url={src}",
        })

    # Inject CSS + serialize
    inject_responsive_css(soup)
    out_html = str(soup)

    return {
        "ok": True,
        "doc_id": doc_id,
        "short_id": short_id,
        "imgs_total": len(replace_imgs),
        "imgs_kept": len(keep),
        "imgs_need_rehost": len(replace_imgs),
        "images": img_list,
        "out_size": len(out_html.encode("utf-8")),
        "html": out_html,
        "log": log,
    }


@app.get("/api/proxy-image")
async def proxy_image(url: str = Query(...)):
    """Proxy download an image from Google with correct referer header.

    This lets users download original images that require Google referer,
    without needing Playwright browser.
    """
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    # Only allow Google-hosted images
    host = urlparse(url).netloc
    allowed_hosts = [
        "docs.google.com", "lh3.googleusercontent.com",
        "lh4.googleusercontent.com", "lh5.googleusercontent.com",
        "lh6.googleusercontent.com", "drive.google.com",
    ]
    if not any(h in host for h in allowed_hosts):
        raise HTTPException(403, f"Proxy only allows Google-hosted images, got: {host}")

    try:
        with httpx.Client(follow_redirects=True, timeout=30) as c:
            resp = c.get(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Referer": "https://docs.google.com/",
            })
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, f"Upstream error: {resp.status_code}")

            ct = resp.headers.get("content-type", "application/octet-stream")
            # Guess filename from URL
            path_part = urlparse(url).path
            filename = unquote(path_part.split("/")[-1]) or "image.png"
            # Remove size suffix like =w2000
            filename = re.sub(r"=[wh]\d+$", "", filename)

            return Response(
                content=resp.content,
                media_type=ct,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "public, max-age=3600",
                },
            )
    except httpx.RequestError as e:
        raise HTTPException(502, f"Failed to fetch image: {e}")


@app.post("/api/download-all")
async def download_all_images(req: FetchRequest):
    """Fetch doc + package all original images as a ZIP file.

    Returns a ZIP with all images named: <short_doc_id>_NNN.ext
    User can unzip, rename if needed, then upload to Drive.
    """
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Thiếu URL")

    try:
        fetched = fetch_full(url)
    except Exception as e:
        raise HTTPException(400, f"Không fetch được doc: {e}")

    doc_id = fetched["doc_id"]
    short_id = short_doc_id(doc_id)

    soup = BeautifulSoup(
        f"<html><head></head><body>{fetched['html']}</body></html>",
        "html.parser",
    )
    for s in soup.find_all(["script", "noscript"]):
        s.decompose()

    _, replace_imgs = classify_images(soup)

    if not replace_imgs:
        raise HTTPException(400, "Doc không có ảnh cần re-host")

    # Download images + build ZIP in memory
    buf = io.BytesIO()
    downloaded = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with httpx.Client(follow_redirects=True, timeout=30) as c:
            for idx, img in enumerate(replace_imgs, 1):
                src = img.get("src", "")
                ext = "png"
                for e in ("png", "jpg", "jpeg", "webp", "gif", "svg"):
                    if f".{e}" in src.lower().split("?")[0]:
                        ext = e
                        break
                filename = f"{short_id}_{idx:03d}.{ext}"
                try:
                    resp = c.get(src, headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://docs.google.com/",
                    })
                    if resp.status_code == 200:
                        zf.writestr(filename, resp.content)
                        downloaded += 1
                except Exception:
                    pass  # skip failed downloads

    buf.seek(0)
    zip_name = f"gdoc2html_{short_id}_{len(replace_imgs)}imgs.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_name}"',
        },
    )


@app.post("/api/render")
async def render(req: RenderRequest):
    """Step 2: Re-fetch doc + Drive rehost → return final HTML with replaced image URLs."""
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Thiếu URL")

    log: list[str] = [f"[render] {url}"]

    try:
        fetched = fetch_full(url)
    except Exception as e:
        raise HTTPException(400, f"Không fetch được doc: {e}")

    doc_id = fetched["doc_id"]
    short_id = short_doc_id(doc_id)
    log.append(f"  doc_id={doc_id}")

    soup = BeautifulSoup(
        f"<html><head></head><body>{fetched['html']}</body></html>",
        "html.parser",
    )
    for s in soup.find_all(["script", "noscript"]):
        s.decompose()
    clean_soup(soup)

    keep, replace_imgs = classify_images(soup)
    log.append(f"  {len(keep)} ảnh lh3 (giữ), {len(replace_imgs)} cần re-host")

    replaced = 0
    drive_files_count = 0
    if req.drive_url and replace_imgs:
        try:
            gapi_key = req.gapi_key or os.environ.get("GAPI_KEY") or ""
            drive_files = list_drive_files(req.drive_url, api_key=gapi_key)
            drive_files_count = len(drive_files)
            log.append(f"  Drive folder có {drive_files_count} file")

            mapping = match_by_index(drive_files, short_id, len(replace_imgs))
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

    inject_responsive_css(soup)
    out_html = str(soup)

    return {
        "ok": True,
        "doc_id": doc_id,
        "short_id": short_id,
        "imgs_total": len(replace_imgs),
        "imgs_kept": len(keep),
        "imgs_replaced": replaced,
        "drive_files_count": drive_files_count,
        "out_size": len(out_html.encode("utf-8")),
        "html": out_html,
        "log": log,
    }
