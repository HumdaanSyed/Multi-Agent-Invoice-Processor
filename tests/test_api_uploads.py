"""Tests for app.uploads - offline, no network, no FastAPI TestClient. Uses
a minimal fake UploadFile (filename + a sync-readable `.file`) matching just
the interface save_upload() actually reads, since the real UploadFile.file
is a SpooledTemporaryFile that behaves identically for `.read(n)`."""

from __future__ import annotations

import io
import os
import time

import pytest

from app.errors import EmptyUpload, UnsupportedMediaType, UploadTooLarge
from app.uploads import (
    DEFAULT_FILENAME,
    max_upload_bytes,
    sanitize_filename,
    save_upload,
    sweep_old_uploads,
)

PDF_HEADER = b"%PDF-1.4\n"


class _FakeUploadFile:
    def __init__(self, filename: str | None, content: bytes) -> None:
        self.filename = filename
        self.file = io.BytesIO(content)


# --- sanitize_filename ------------------------------------------------


def test_sanitize_filename_leaves_a_normal_name_alone():
    assert sanitize_filename("invoice.pdf") == "invoice.pdf"


def test_sanitize_filename_strips_path_traversal_to_basename():
    assert sanitize_filename("../../etc/passwd") == "passwd"


@pytest.mark.parametrize("value", [None, "", "."])
def test_sanitize_filename_falls_back_on_empty_basename(value):
    assert sanitize_filename(value) == DEFAULT_FILENAME


def test_sanitize_filename_falls_back_on_bare_dotdot():
    # Path("..").name == ".." (not "") - pathlib does not collapse this to
    # empty the way it does for "." - so this must be checked explicitly
    # rather than relying on the empty-string guard alone.
    assert sanitize_filename("..") == DEFAULT_FILENAME


def test_sanitize_filename_strips_unsafe_characters():
    assert sanitize_filename("my invoice (final)!.pdf") == "myinvoicefinal.pdf"


def test_sanitize_filename_falls_back_when_entirely_unsafe():
    assert sanitize_filename("???") == DEFAULT_FILENAME


def test_sanitize_filename_truncates_long_names():
    long_name = "a" * 200 + ".pdf"
    result = sanitize_filename(long_name)
    assert len(result) == 100


# --- save_upload ------------------------------------------------------


def test_save_upload_writes_valid_pdf(tmp_path):
    content = PDF_HEADER + b"rest of file"
    upload = _FakeUploadFile("invoice.pdf", content)
    path = save_upload(upload, thread_id="t1", upload_dir=tmp_path)
    assert path.name == "t1_invoice.pdf"
    assert path.read_bytes() == content


def test_save_upload_sanitizes_traversal_filename(tmp_path):
    upload = _FakeUploadFile("../../etc/passwd.pdf", PDF_HEADER)
    path = save_upload(upload, thread_id="t2", upload_dir=tmp_path)
    assert path == tmp_path / "t2_passwd.pdf"
    assert path.parent == tmp_path


def test_save_upload_rejects_non_pdf_and_cleans_up(tmp_path):
    upload = _FakeUploadFile("fake.pdf", b"not a pdf at all")
    with pytest.raises(UnsupportedMediaType):
        save_upload(upload, thread_id="t3", upload_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_save_upload_rejects_empty_file_and_cleans_up(tmp_path):
    upload = _FakeUploadFile("empty.pdf", b"")
    with pytest.raises(EmptyUpload):
        save_upload(upload, thread_id="t4", upload_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_save_upload_rejects_oversized_file_and_cleans_up(tmp_path):
    content = PDF_HEADER + b"x" * 100
    upload = _FakeUploadFile("big.pdf", content)
    with pytest.raises(UploadTooLarge):
        save_upload(upload, thread_id="t5", upload_dir=tmp_path, max_bytes=10)
    assert list(tmp_path.iterdir()) == []


def test_save_upload_rejects_too_short_to_contain_magic(tmp_path):
    upload = _FakeUploadFile("tiny.pdf", b"%PD")  # shorter than "%PDF-"
    with pytest.raises(UnsupportedMediaType):
        save_upload(upload, thread_id="t6", upload_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- max_upload_bytes ---------------------------------------------------


def test_max_upload_bytes_default(monkeypatch):
    monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
    assert max_upload_bytes() == 20 * 1024 * 1024


def test_max_upload_bytes_from_env(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "12345")
    assert max_upload_bytes() == 12345


def test_max_upload_bytes_falls_back_on_garbage_env(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "not-a-number")
    assert max_upload_bytes() == 20 * 1024 * 1024


# --- sweep_old_uploads ----------------------------------------------------


def test_sweep_old_uploads_removes_only_stale_files(tmp_path):
    old_file = tmp_path / "old_invoice.pdf"
    new_file = tmp_path / "new_invoice.pdf"
    old_file.write_bytes(PDF_HEADER)
    new_file.write_bytes(PDF_HEADER)

    old_time = time.time() - 25 * 3600  # 25h old
    os.utime(old_file, (old_time, old_time))

    removed = sweep_old_uploads(tmp_path, max_age_hours=24)

    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_sweep_old_uploads_missing_dir_is_a_noop(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert sweep_old_uploads(missing) == 0
