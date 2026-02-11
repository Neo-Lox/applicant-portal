import uuid
import os
import tempfile
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from . import supabase


def _is_vercel() -> bool:
    return str(os.environ.get("VERCEL") or "").strip().lower() in {"1", "true", "yes"} or bool(
        os.environ.get("VERCEL_ENV")
    )


def _tmp_uploads_root() -> Path:
    # Vercel (and many serverless platforms) only guarantee /tmp as writable.
    return Path(tempfile.gettempdir()) / "applicant_portal_uploads"


def _local_uploads_root() -> Path:
    return Path(current_app.instance_path) / "uploads"


def _uploads_root() -> Path:
    """
    Decide where uploads should be written.

    - Local dev: `instance/uploads`
    - Vercel/serverless: fall back to `/tmp` to avoid read-only filesystem crashes
    """
    storage_mode = (current_app.config.get("STORAGE_MODE") or "local").strip().lower()
    if storage_mode in {"tmp", "temp", "ephemeral"}:
        return _tmp_uploads_root()

    # Default: "local"
    root = _local_uploads_root()
    if _is_vercel():
        return _tmp_uploads_root()
    return root


def save_file(file_storage, application_id: int) -> dict:
    """
    Save uploaded file to storage (Supabase or local filesystem).
    
    Returns dict with file_url, file_name, and file_type.
    """
    storage_mode = (current_app.config.get("STORAGE_MODE") or "local").strip().lower()
    
    if storage_mode == "supabase":
        return _save_file_supabase(file_storage, application_id)
    else:
        return _save_file_local(file_storage, application_id)


def _save_file_supabase(file_storage, application_id: int) -> dict:
    """Save file to Supabase Storage."""
    extension = Path(file_storage.filename or "").suffix.lower()
    safe_name = secure_filename(file_storage.filename or "upload")
    object_path = f"{application_id}/{uuid.uuid4().hex}_{safe_name}"
    if extension and not object_path.endswith(extension):
        object_path += extension

    # Read file content
    stream = getattr(file_storage, "stream", None)
    if stream is not None:
        try:
            stream.seek(0)
        except Exception:
            pass
        file_bytes = stream.read()
    else:
        # Fallback (should be rare)
        try:
            file_bytes = file_storage.read()
        except Exception:
            file_bytes = b""
    
    # Upload to Supabase Storage
    supabase.storage_upload_bytes(
        object_path=object_path,
        file_bytes=file_bytes,
        content_type=file_storage.mimetype,
        upsert=False,
    )
    
    # Store the object path (we'll generate signed URLs on-demand)
    return {
        "file_url": object_path,  # Store path, not full URL
        "file_name": file_storage.filename or "upload",
        "file_type": file_storage.mimetype or None,
    }


def _save_file_local(file_storage, application_id: int) -> dict:
    """Save file to local filesystem (legacy)."""
    uploads_root = _uploads_root()
    try:
        uploads_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Last-resort fallback (e.g. instance_path not writable)
        uploads_root = _tmp_uploads_root()
        uploads_root.mkdir(parents=True, exist_ok=True)

    extension = Path(file_storage.filename or "").suffix.lower()
    safe_name = secure_filename(file_storage.filename or "upload")
    object_name = f"{application_id}/{uuid.uuid4().hex}_{safe_name}"
    if extension and not object_name.endswith(extension):
        object_name += extension

    destination = uploads_root / object_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(destination)
    return {
        "file_url": str(destination),
        "file_name": file_storage.filename or "upload",
        "file_type": file_storage.mimetype or None,
    }


def get_file_url(file_path: str, expires_in: int = 120) -> str:
    """
    Get a URL for accessing a stored file.
    
    For Supabase storage, generates a signed URL.
    For local storage, returns the file path as-is.
    """
    storage_mode = (current_app.config.get("STORAGE_MODE") or "local").strip().lower()
    
    if storage_mode == "supabase":
        # file_path is the object path in Supabase Storage
        return supabase.storage_url(file_path, expires_in=expires_in)
    else:
        # file_path is the local filesystem path
        return file_path


def delete_file(file_path: str) -> None:
    """
    Delete a stored file.
    
    For Supabase storage, deletes from bucket.
    For local storage, deletes from filesystem.
    """
    storage_mode = (current_app.config.get("STORAGE_MODE") or "local").strip().lower()
    
    if storage_mode == "supabase":
        try:
            supabase.storage_delete_object(file_path)
        except supabase.SupabaseAPIError:
            pass  # Ignore errors (file might not exist)
    else:
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception:
            pass  # Ignore errors


def filestorage_size_bytes(file_storage) -> int | None:
    """
    Best-effort size detection for Werkzeug `FileStorage`.

    Returns size in bytes, or None if unknown.
    """
    if file_storage is None:
        return None

    # Prefer provided content_length, but only if it's > 0
    try:
        cl = getattr(file_storage, "content_length", None)
        if cl is not None:
            cl_int = int(cl)
            if cl_int > 0:
                return cl_int
    except Exception:
        pass

    stream = getattr(file_storage, "stream", None)
    if stream is None:
        return None

    # Try seeking to end
    try:
        pos = stream.tell()
        stream.seek(0, 2)  # end
        end = stream.tell()
        stream.seek(pos)
        if end is not None:
            return int(end)
    except Exception:
        pass

    # Fallback: read stream (may be expensive; used only if needed)
    try:
        pos = stream.tell()
        try:
            stream.seek(0)
        except Exception:
            return None
        total = 0
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            total += len(chunk)
        try:
            stream.seek(pos)
        except Exception:
            pass
        return int(total)
    except Exception:
        return None
