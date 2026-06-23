"""HTML cleaning, image classification, and responsive CSS injection."""

from urllib.parse import urlparse
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
    """Return (keep, replace) lists of <img> tags."""
    keep, replace = [], []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        host = urlparse(src).netloc
        if any(h in host for h in KEEP_HOSTS):
            keep.append(img)
        else:
            replace.append(img)
    return keep, replace
