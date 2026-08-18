"""Upload handling: streaming size cap, content sniffing, filename
sanitization, and opportunistic cleanup of old uploaded PDFs.

Reads happen on the underlying sync file object (`UploadFile.file`), not
the async `UploadFile.read()` wrapper - this backend's endpoints are sync
`def` handlers (forced by SqliteSaver having no working async methods; see
app/service.py's module docstring), so no coroutine is available at the
call site. FastAPI still resolves the multipart body into a real
SpooledTemporaryFile before calling into the sync handler, so `.file.read()`
works the same as any other file object.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from fastapi import UploadFile

from app.errors import ApiError, EmptyUpload, UnsupportedMediaType, UploadTooLarge

CHUNK_SIZE = 1024 * 1024  # 1 MiB
PDF_MAGIC = b"%PDF-"
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB - see docs/api.md for
# why 20 MB: Anthropic's request payload limit is 32 MB and a PDF is sent
# base64-encoded (x1.37) plus prompt overhead, so anything close to 32 MB
# raw would fail at Anthropic *after* we've already spent the upload time.

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
DEFAULT_FILENAME = "upload.pdf"
MAX_FILENAME_LENGTH = 100


def max_upload_bytes() -> int:
    raw = os.environ.get("MAX_UPLOAD_BYTES")
    if not raw:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES


def sanitize_filename(filename: str | None) -> str:
    """Reduce an untrusted, client-supplied filename to a safe basename.

    `Path(x).name` strips any directory components, so a traversal value
    like "../../etc/passwd" resolves to "passwd" rather than escaping the
    upload directory - same pattern as
    invoice_agent/mcp_servers/filesystem_invoices.py's `_safe_inbox_path`.
    `.name` alone is not sufficient, and not only for the empty-string case:
    `Path(".").name == ""`, but `Path("..").name == ".."` (pathlib does NOT
    collapse a bare ".." to empty - only "." and "" do). This filename is
    only ever used as a literal suffix after a `{thread_id}_` prefix, not
    path-joined, so a literal ".." component isn't actually a traversal
    today - but that safety would silently depend on the exact storage-path
    format never changing, so reject "." and ".." explicitly here instead
    of relying on it.
    """
    name = Path(filename or "").name
    if not name or name in {".", ".."}:
        return DEFAULT_FILENAME
    name = _SAFE_CHARS.sub("", name)[:MAX_FILENAME_LENGTH]
    return name or DEFAULT_FILENAME  # e.g. filename was entirely unsafe chars


def save_upload(
    file: UploadFile,
    *,
    thread_id: str,
    upload_dir: Path,
    max_bytes: int | None = None,
) -> Path:
    """Stream an uploaded file to `upload_dir` as `{thread_id}_{safe_name}`,
    enforcing a size cap and a magic-bytes content check as it goes.

    Content-Type is never trusted alone - it's client-supplied, and browsers
    routinely send `application/octet-stream` for legitimate PDFs (REVIEW.md's
    "match the loosest correct signal" pattern). The first chunk's leading
    bytes are checked against the real PDF magic number instead.

    Raises (and cleans up the partial file for) `UnsupportedMediaType`,
    `UploadTooLarge`, or `EmptyUpload` from app/errors - callers don't need
    their own try/except around content validation.
    """
    cap = max_bytes if max_bytes is not None else max_upload_bytes()
    upload_dir.mkdir(parents=True, exist_ok=True)
    out_path = upload_dir / f"{thread_id}_{sanitize_filename(file.filename)}"

    total = 0
    checked_magic = False
    try:
        with open(out_path, "wb") as out:
            while True:
                chunk = file.file.read(CHUNK_SIZE)
                if not chunk:
                    break
                if not checked_magic:
                    if len(chunk) < len(PDF_MAGIC) or not chunk.startswith(PDF_MAGIC):
                        raise UnsupportedMediaType("Uploaded file is not a PDF (missing %PDF- header).")
                    checked_magic = True
                total += len(chunk)
                if total > cap:
                    raise UploadTooLarge(f"Upload exceeds the {cap}-byte limit.")
                out.write(chunk)
    except ApiError:
        out_path.unlink(missing_ok=True)
        raise

    if total == 0:
        out_path.unlink(missing_ok=True)
        raise EmptyUpload("Uploaded file is empty.")

    return out_path


def sweep_old_uploads(upload_dir: Path, *, max_age_hours: float = 24) -> int:
    """Delete uploaded files older than `max_age_hours`. Called
    opportunistically at the top of POST /invoices rather than run on a
    scheduler or background thread - no extra process for a demo's traffic.

    Consequence, documented rather than solved: retrying a >24h-old failed
    run (an empty-body resume) fails with FileNotFoundError trying to
    re-read the swept PDF - that flows through output()'s/router's existing
    error handling as a `failed` status, a discoverable failure rather than
    a silent one.
    """
    if not upload_dir.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in upload_dir.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
