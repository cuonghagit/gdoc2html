"""HTML cleaning, image classification, base64 inlining, and responsive CSS injection."""

import base64
from io import BytesIO
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


GOOGLE_CLASS_PREFIXES = ("title-", "subtitle-", "doc-", "c-")
INLINE_STYLE_DROP = ("width", "height")


def clean_soup(soup: BeautifulSoup) -> None:
    """Strip Google-specific classes & inline width/height."""
    for tag in soup.find_all(True):
        classes = tag.get("class") or []
        kept = [c for c in classes if not any(c.startswith(p) for p in GOOGLE_CLASS_PREFIXES)]
        if kept:
            tag["class"] = kept
        elif "class" in tag.attrs:
            del tag["class"]

        if "style" in tag.attrs:
            style = tag.get("style", "")
            new_parts = []
            for part in style.split(";"):
                part = part.strip()
                if not part:
                    continue
                key = part.split(":", 1)[0].strip().lower()
                if key in INLINE_STYLE_DROP:
                    continue
                new_parts.append(part)
            if new_parts:
                tag["style"] = "; ".join(new_parts)
            else:
                del tag["style"]


def inject_responsive_css(soup: BeautifulSoup) -> None:
    """Inject responsive CSS into document head."""
    css = (
        "body { max-width:900px; margin:0 auto; padding:20px 30px; "
        "line-height:1.7; font-size:16px; font-family: -apple-system, "
        "BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color:#222; }\n"
        "img { max-width:100%; height:auto; display:block; margin:1em auto; }\n"
        "pre, code { max-width:100%; overflow-x:auto; }\n"
        "table { max-width:100%; overflow-x:auto; display:block; }\n"
        "h1, h2, h3, h4 { line-height:1.3; }\n"
        "a { color:#1a73e8; }\n"
        "@media (max-width:600px) { body { padding:15px; font-size:15px; } }\n"
    )
    style = soup.new_tag("style")
    style.string = css
    if soup.head:
        soup.head.append(style)
    else:
        head = soup.new_tag("head")
        head.append(style)
        soup.insert(0, head)


KEEP_HOSTS = (
    "lh3.googleusercontent.com", "lh4.googleusercontent.com",
    "lh5.googleusercontent.com", "lh6.googleusercontent.com",
)


def classify_images(soup: BeautifulSoup):
    """Return (keep, replace) lists of <img> tags.
    
    'keep' = already on lh3 (works everywhere)
    'replace' = on docs.google.com (needs referer or base64)
    """
    keep, replace = [], []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        host = urlparse(src).netloc
        if any(h in host for h in KEEP_HOSTS):
            keep.append(img)
        else:
            replace.append(img)
    return keep, replace


def inline_images_base64(
    replace_imgs: list,
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    timeout: int = 60,
) -> dict:
    """Download images from Google and replace src with base64 data URIs.
    
    Returns: {
        "inlined": int,          # successfully inlined
        "failed": int,           # download/encode failures
        "size_bytes": int,       # total image data size
        "errors": list[str],     # per-image error messages
    }
    """
    result = {"inlined": 0, "failed": 0, "size_bytes": 0, "errors": []}
    
    if not replace_imgs:
        return result

    with httpx.Client(follow_redirects=True, timeout=timeout) as c:
        for img in replace_imgs:
            src = img.get("src", "")
            if not src:
                continue
            
            ext = "png"
            for e in ("png", "jpg", "jpeg", "webp", "gif", "svg"):
                if f".{e}" in src.lower().split("?")[0]:
                    ext = e
                    break
            # Normalize jpeg → jpg for MIME type
            mime_map = {"jpeg": "jpeg", "jpg": "jpeg", "png": "png", "gif": "gif",
                       "webp": "webp", "svg": "svg+xml"}
            mime = mime_map.get(ext, "png")

            try:
                resp = c.get(src, headers={
                    "User-Agent": user_agent,
                    "Referer": "https://docs.google.com/",
                })
                if resp.status_code != 200:
                    result["failed"] += 1
                    result["errors"].append(f"[{ext}] HTTP {resp.status_code}: {src[:80]}")
                    continue

                data = resp.content
                b64 = base64.b64encode(data).decode("ascii")
                img["src"] = f"data:image/{mime};base64,{b64}"
                result["inlined"] += 1
                result["size_bytes"] += len(data)
                
            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"[{ext}] {e}: {src[:80]}")
    
    return result
