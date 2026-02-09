from __future__ import annotations

import hashlib
import re
import urllib.request
from pathlib import Path
from typing import Optional, Tuple


def _sanitize_filename(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "file"


def build_pdf_filename(title: Optional[str], author: Optional[str], year: Optional[int]) -> str:
    parts = [p for p in [title, author, str(year) if year else None] if p]
    base = _sanitize_filename("_".join(parts))
    return f"{base}.pdf"


def download_pdf(url: str, out_dir: Path, filename: str, timeout: float) -> Tuple[Optional[Path], dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / filename
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        index = 1
        while True:
            candidate = out_dir / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                target = candidate
                break
            index += 1

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.info().get_content_type()
            if content_type != "application/pdf":
                return None, {
                    "status": "skipped",
                    "error": f"content_type_not_pdf:{content_type}",
                }

            data = response.read()
            sha256 = hashlib.sha256(data).hexdigest()
            with open(target, "wb") as handle:
                handle.write(data)

            return target, {
                "status": "success",
                "size_bytes": len(data),
                "sha256": sha256,
                "content_type": content_type,
            }
    except Exception as exc:
        return None, {
            "status": "failed",
            "error": str(exc),
        }
