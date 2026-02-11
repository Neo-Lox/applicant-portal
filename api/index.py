"""
Vercel entrypoint for this Flask app.

Vercel's Python runtime runs code from the `api/` directory as serverless functions.
We expose a WSGI app called `app` so the runtime can serve Flask.
"""

from __future__ import annotations

import sys
from pathlib import Path


# Ensure repository root is importable when running from `api/`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# Export the WSGI app for Vercel.
from wsgi import app  # noqa: E402  (import after sys.path tweak)

