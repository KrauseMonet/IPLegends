"""Vercel entrypoint. Vercel's Python runtime looks for an ASGI app named `app` in this
file; the real application lives in `web.app` and is only re-exported here, so there is
exactly one FastAPI app in the codebase rather than a second copy of the routes."""

from web.app import app  # noqa: F401
