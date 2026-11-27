"""Serves the operator console (a static page, no external assets)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

CONSOLE_FILE = Path(__file__).parent / "console" / "index.html"

router = APIRouter(tags=["console"])


@router.get("/console", response_class=HTMLResponse)
def console() -> HTMLResponse:
    """Deliberately public: the page itself only renders data the API authorises."""

    return HTMLResponse(
        content=CONSOLE_FILE.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, must-revalidate"},
    )
