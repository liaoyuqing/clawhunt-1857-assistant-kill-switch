"""Acceptance criterion 2: an in-flight SSE stream ends within 5s of a disable.

The test opens a real streaming request with httpx against a live uvicorn server
(not the in-process TestClient, which buffers), then flips the global switch
from the admin API and measures the wall clock until the finish frame arrives.
It also proves the stream was not empty before the disable: tokens > 0.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.cache import CacheStats, InMemorySwitchCache
from app.config import Settings
from app.db import Database, DatabaseStats
from app.main import create_app


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def live_server(tmp_path: Path) -> Iterator[tuple[str, FastAPI]]:
    db_path = tmp_path / "sse.sqlite"
    settings = Settings(
        database_url="sqlite+pysqlite:///" + str(db_path).replace("\\", "/"),
        redis_url="",
        cache_ttl_seconds=5.0,
        sse_frame_interval_seconds=0.05,
        sse_max_frames=120,
    )
    database = Database(settings, DatabaseStats())
    database.create_all()
    cache = InMemorySwitchCache("asst", CacheStats(), ttl_seconds=5.0)
    app = create_app(settings, database=database, cache=cache, environ={})

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"

    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        database.dispose()


def test_in_flight_stream_ends_within_five_seconds(live_server: tuple[str, FastAPI]) -> None:
    base_url, _ = live_server
    auth = {"Authorization": "Bearer tok-staff-hangzhou"}
    tokens = 0
    finish: dict[str, object] = {}
    started = time.monotonic()

    with httpx.Client(timeout=30.0) as client, client.stream(
        "POST",
        base_url + "/api/assistant/chat",
        json={"prompt": "整理本周会员企业资源对接进展", "stream": True},
        headers=auth,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        disabled_at: float | None = None
        event = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if event == "token":
                    tokens += 1
                    if disabled_at is None and tokens >= 3:
                        # Disable while the answer is still streaming. The clock starts
                        # before the admin call so the measured latency covers the write,
                        # the invalidation and the remaining frames - not just the last hop.
                        disabled_at = time.monotonic()
                        with httpx.Client(timeout=10.0) as admin:
                            put = admin.put(
                                base_url + "/api/assistant/admin/switches",
                                json={
                                    "scope": "global",
                                    "scope_ref": "",
                                    "enabled": False,
                                    "reason": "演练：流式会话中关闭助手",
                                },
                                headers={"Authorization": "Bearer tok-admin-zhou"},
                            )
                        assert put.status_code == 201
                elif event == "finish":
                    finish = payload
                    break

    assert disabled_at is not None
    assert tokens > 0, "the stream must have produced tokens before the disable"
    assert finish.get("finish_reason") == "disabled"
    assert finish.get("source") == "global"
    assert finish.get("tokens") == tokens
    latency = time.monotonic() - disabled_at
    print(
        f"AC2_EVIDENCE tokens={tokens} finish_reason={finish.get('finish_reason')} "
        f"source={finish.get('source')} latency_ms={latency * 1000:.0f}"
    )
    assert latency < 5.0, f"stream took {latency:.2f}s to end after the disable"
    assert time.monotonic() - started < 30.0


def test_stream_is_refused_when_already_disabled(live_server: tuple[str, FastAPI]) -> None:
    base_url, _ = live_server
    admin = {"Authorization": "Bearer tok-admin-zhou"}
    with httpx.Client(timeout=10.0) as client:
        client.put(
            base_url + "/api/assistant/admin/switches",
            json={"scope": "global", "scope_ref": "", "enabled": False, "reason": "提前关闭"},
            headers=admin,
        )
        response = client.post(
            base_url + "/api/assistant/chat",
            json={"prompt": "流式请求", "stream": True},
            headers={"Authorization": "Bearer tok-staff-hangzhou"},
        )
    assert response.status_code == 503
    assert response.json()["code"] == "ASSISTANT_DISABLED"


def test_finish_frame_reports_a_finished_stream(live_server: tuple[str, FastAPI]) -> None:
    """Without a disable the stream ends normally with finish_reason=stop."""

    base_url, _ = live_server
    events: list[str] = []
    finish: dict[str, object] = {}
    with httpx.Client(timeout=30.0) as client, client.stream(
        "POST",
        base_url + "/api/assistant/chat",
        json={"prompt": "生成一份会员服务周报提纲", "stream": True},
        headers={"Authorization": "Bearer tok-staff-hangzhou"},
    ) as response:
        assert response.status_code == 200
        event = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[7:].strip()
                events.append(event)
            elif line.startswith("data: ") and event == "finish":
                finish = json.loads(line[6:])
                break
    assert finish["finish_reason"] == "stop"
    assert int(str(finish["tokens"])) > 0
    assert "start" in events and "token" in events
