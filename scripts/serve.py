"""Local runner: reads ASST_* environment variables and serves the API + console."""

from __future__ import annotations

import uvicorn

from app.config import force_off_enabled, load_settings
from app.db import Database
from app.main import build_cache, create_app


def main() -> None:
    settings = load_settings()
    database = Database(settings)
    cache = build_cache(settings)
    app = create_app(settings, database=database, cache=cache)
    print(
        f"starting on http://127.0.0.1:8087  cache={cache.backend}  "
        f"ttl={settings.cache_ttl_seconds}s  force_off={force_off_enabled()}"
    )
    uvicorn.run(app, host="127.0.0.1", port=8087, log_level="info")


if __name__ == "__main__":
    main()
