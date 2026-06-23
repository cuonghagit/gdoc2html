"""Google Drive folder listing via Drive API v3 (httpx, no browser)."""

import re
import httpx


DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


def extract_folder_id(url: str) -> str:
    """Extract folder ID from any Drive folder URL."""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract Drive folder ID from: {url}")


def list_drive_files(
    folder_url: str,
    api_key: str | None = None,
) -> list[dict]:
    """List files in a public Drive folder via API v3.

    Requires either an API key (GAPI_KEY) or the folder must be public
    and accessible anonymously.

    Returns list of {id, name}.
    """
    folder_id = extract_folder_id(folder_url)

    if api_key:
        return _list_via_api(folder_id, api_key)

    # Try anonymous access
    return _list_via_api(folder_id, "")


def _list_via_api(folder_id: str, api_key: str) -> list[dict]:
    """Use Drive API v3 to list files in folder.

    If api_key is empty, attempt anonymous access.
    """
    params = {
        "q": f"'{folder_id}' in parents and trashed=false",
        "fields": "files(id,name)",
        "pageSize": 100,
        "orderBy": "name",
    }
    if api_key:
        params["key"] = api_key

    with httpx.Client(follow_redirects=True, timeout=15) as c:
        resp = c.get(f"{DRIVE_API_BASE}/files", params=params)
        if resp.status_code == 403 and not api_key:
            raise RuntimeError(
                "Drive folder access requires an API key. "
                "Set GAPI_KEY environment variable or provide ?gapi_key= parameter. "
                "Get one at https://console.cloud.google.com/apis/credentials"
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Drive API error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return data.get("files", [])


def drive_id_to_lh3(file_id: str, size: str = "w2000") -> str:
    """Convert Drive file ID to lh3 URL."""
    return f"https://lh3.googleusercontent.com/d/{file_id}={size}"


def match_by_index(
    drive_files: list[dict],
    doc_short_id: str,
    count: int,
) -> dict[int, str]:
    """Match Drive files to image indices by naming convention.

    Expects filenames: <doc_short_id>_NNN.ext (e.g. 'abc123_001.png')
    Returns {1-based_idx: lh3_url}
    """
    by_stem: dict[str, str] = {}
    for f in drive_files:
        name = f.get("name", "")
        # Strip extension, lowercase
        stem = name.rsplit(".", 1)[0].lower() if "." in name else name.lower()
        by_stem[stem] = f["id"]

    mapping: dict[int, str] = {}
    for idx in range(1, count + 1):
        stem = f"{doc_short_id}_{idx:03d}".lower()
        fid = by_stem.get(stem)
        if not fid:
            # Try with common extensions
            for ext in ("png", "jpg", "jpeg", "webp", "gif", "svg"):
                cand = f"{stem}.{ext}"
                for f in drive_files:
                    if f.get("name", "").lower() == cand:
                        fid = f["id"]
                        break
                if fid:
                    break
        if fid:
            mapping[idx] = drive_id_to_lh3(fid)

    return mapping


def build_suggested_names(doc_short_id: str, count: int) -> list[str]:
    """Generate suggested filenames for indexing."""
    return [f"{doc_short_id}_{i:03d}.png" for i in range(1, count + 1)]
