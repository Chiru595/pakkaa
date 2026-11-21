"""Vercel Python function entry point: serves the FastAPI app for every /api/* request."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from app.main import app  # noqa: E402

__all__ = ["app"]
