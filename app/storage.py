import uuid
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename


def save_file(file_storage, application_id: int) -> dict:
    # Storage abstraction placeholder: currently local filesystem only.
    uploads_root = Path(current_app.instance_path) / "uploads"
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
