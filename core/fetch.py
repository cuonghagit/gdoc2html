"""Fetch Google Doc published HTML via httpx (no Playwright)."""

import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


def extract_doc_id(url: str) -> str:
    """Get the Google Doc id from any URL form."""
    m = re.search(r"/document/d/(?:e/)?([a-zA-Z0-9_-]{20,})", url)
    if not m:
        raise ValueError(f"Cannot find doc id in URL: {url}")
    return m.group(1)


def is_published_url(url: str) -> bool:
    return "/d/e/" in url


def fetch_doc_html(url: str, timeout: int = 30) -> tuple[str, str]:
    """Fetch published Google Doc HTML.

    Returns (html_content, mode) where mode is 'pub' or 'export'.
    Tries /pub first, falls back to /export?format=html.
    """
    doc_id = extract_doc_id(url)
    mode = ""

    with httpx.Client(follow_redirects=True, timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}) as c:
        # 1) Try /pub
        if is_published_url(url):
            pub_url = f"https://docs.google.com/document/d/e/{doc_id}/pub"
        else:
            pub_url = f"https://docs.google.com/document/d/{doc_id}/pub"

        resp = c.get(pub_url)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            contents = soup.select_one("#contents")
            if contents:
                html = contents.decode_contents()
                if len(html) > 200:
                    return html, "pub"

        # 2) Fallback /export
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=html"
        resp = c.get(export_url)
        if resp.status_code == 200 and len(resp.text) > 200:
            return resp.text, "export"

    raise RuntimeError("Failed to fetch doc HTML from both /pub and /export")


def fetch_full(url: str, timeout: int = 60) -> dict:
    """Fetch doc HTML + extract image URLs.

    Returns {"html": str, "image_urls": list[str], "doc_id": str}.
    """
    doc_id = extract_doc_id(url)
    html, mode = fetch_doc_html(url, timeout=timeout)

    # Parse and find all img src
    soup_light = BeautifulSoup(f"<html><body>{html}</body></html>", "html.parser")
    image_urls = [
        img["src"] for img in soup_light.find_all("img")
        if img.get("src") and not img["src"].startswith("data:")
    ]

    return {"html": html, "image_urls": image_urls, "doc_id": doc_id, "mode": mode}
