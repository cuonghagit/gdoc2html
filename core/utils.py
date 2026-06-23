"""Filename helpers."""

import re
import unicodedata
from pathlib import Path


def extract_doc_id(url: str) -> str:
    m = re.search(r"/document/d/(?:e/)?([a-zA-Z0-9_-]{20,})", url)
    if not m:
        raise ValueError(f"Cannot find doc id in URL: {url}")
    return m.group(1)


def short_doc_id(doc_id: str, n: int = 8) -> str:
    """Truncate Google doc ID to short tag for filenames."""
    return doc_id[:n] if len(doc_id) > n else doc_id


def suggested_filename(doc_id: str, idx: int, ext: str = "png") -> str:
    return f"{short_doc_id(doc_id)}_{idx:03d}.{ext}"


def build_suggested_names(doc_short_id: str, count: int) -> list[str]:
    return [f"{doc_short_id}_{i:03d}.png" for i in range(1, count + 1)]


def sanitize_title(title: str, max_len: int = 100) -> str:
    t = unicodedata.normalize("NFKC", title).strip()
    t = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "", t)
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"_+", "_", t)
    t = t.strip("._-")
    if not t:
        t = "untitled"
    if len(t) > max_len:
        t = t[:max_len].rstrip("._-")
    return t


def unique_dir(base: Path) -> Path:
    if not base.exists():
        return base
    i = 1
    while True:
        cand = base.parent / f"{base.name}({i})"
        if not cand.exists():
            return cand
        i += 1
