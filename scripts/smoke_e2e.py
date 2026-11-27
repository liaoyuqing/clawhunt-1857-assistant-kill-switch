"""End-to-end smoke against a real PostgreSQL 16 instance.

Runs the four acceptance scenarios over HTTP (a real uvicorn server, a real
database, no mocks inside the request path) and prints one line per assertion.
The pytest suite covers the same ground with fixtures; this script exists so the
delivery can show the behaviour against PostgreSQL rather than SQLite.

Usage:
    ASST_DATABASE_URL=postgresql+psycopg://... python scripts/smoke_e2e.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cache import CacheStats, InMemorySwitchCache  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import Database, DatabaseStats  # noqa: E402
from app.main import create_app  # noqa: E402

RESULTS: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append({"check": name, "ok": ok, "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + " :: " + detail, flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    url = os.environ.get("ASST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        print("ASST_DATABASE_URL must point at PostgreSQL for this smoke run")
        return 2
    settings = Settings(
        database_url=url,
        redis_url="",
        cache_ttl_seconds=5.0,
        sse_frame_interval_seconds=0.05,
        sse_max_frames=120,
    )
    database = Database(settings, DatabaseStats())
    cache = InMemorySwitchCache("asst", CacheStats(), 5.0)
    app = create_app(settings, database=database, cache=cache, environ={})
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    base_url = f"http://127.0.0.1:{port}"
    admin = {"Authorization": "Bearer tok-admin-zhou"}
    hz = {"Authorization": "Bearer tok-staff-hangzhou"}
    sz = {"Authorization": "Bearer tok-staff-suzhou"}

    try:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            # Reset the platform switch state so the run starts from "all on".
            for scope, ref in [("global", ""), ("site", "site-hangzhou"), ("site", "site-suzhou"),
                               ("role", "operation"), ("tool", "sop_draft")]:
                client.put(
                    "/api/assistant/admin/switches",
                    json={"scope": scope, "scope_ref": ref, "enabled": True, "reason": "冒烟前复位"},
                    headers=admin,
                )
            listed = client.get("/api/assistant/admin/switches", headers=admin).json()
            record("seed/reset", listed["count"] == 5, f"asst_switch rows = {listed['count']}")

            # AC3: site isolation.
            client.put(
                "/api/assistant/admin/switches",
                json={"scope": "site", "scope_ref": "site-hangzhou", "enabled": False,
                      "reason": "杭州站点机房迁移"},
                headers=admin,
            )
            r_hz = client.post("/api/assistant/chat", json={"prompt": "会员合同模板"}, headers=hz)
            r_sz = client.post("/api/assistant/chat", json={"prompt": "会员合同模板"}, headers=sz)
            record(
                "AC3 site isolation",
                r_hz.status_code == 503 and r_sz.status_code == 200,
                f"hangzhou={r_hz.status_code}({r_hz.json().get('code')}) suzhou={r_sz.status_code}",
            )

            # AC2: an in-flight SSE stream ends within 5 seconds.
            tokens = 0
            finish: dict[str, object] = {}
            disabled_at = 0.0
            with client.stream(
                "POST", base_url + "/api/assistant/chat",
                json={"prompt": "整理本周资源对接进展", "stream": True}, headers=sz,
            ) as response:
                event = ""
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[7:].strip()
                    elif line.startswith("data: "):
                        payload = json.loads(line[6:])
                        if event == "token":
                            tokens += 1
                            if tokens == 3:
                                disabled_at = time.monotonic()
                                client.put(
                                    "/api/assistant/admin/switches",
                                    json={"scope": "global", "scope_ref": "", "enabled": False,
                                          "reason": "冒烟：流中关闭"},
                                    headers=admin,
                                )
                        elif event == "finish":
                            finish = payload
                            break
            latency = time.monotonic() - disabled_at
            record(
                "AC2 SSE finish_reason=disabled",
                finish.get("finish_reason") == "disabled" and tokens > 0 and latency < 5.0,
                f"tokens={tokens} reason={finish.get('finish_reason')} "
                f"latency={latency * 1000:.0f}ms checks={finish.get('checks')}",
            )

            # Closed chat answers 503 with the documented code.
            r = client.post("/api/assistant/chat", json={"prompt": "关闭后还能提问吗"}, headers=sz)
            record(
                "closed chat -> 503 ASSISTANT_DISABLED",
                r.status_code == 503 and r.json()["code"] == "ASSISTANT_DISABLED",
                f"HTTP {r.status_code} {r.json()['code']} source={r.json()['source']}",
            )

            # AC4: the recruitment module is untouched.
            tasks = client.get("/api/recruitment/ai/tasks", headers=sz)
            run = client.post("/api/recruitment/ai/tasks/rt-2001/run", headers=sz)
            record(
                "AC4 recruitment AI unaffected",
                tasks.status_code == 200 and run.status_code == 200,
                f"tasks={tasks.status_code} run={run.status_code} "
                f"leads={run.json().get('result', {}).get('leads_scored')}",
            )

            # Reopening restores the assistant.
            client.put(
                "/api/assistant/admin/switches",
                json={"scope": "global", "scope_ref": "", "enabled": True, "reason": "冒烟复位"},
                headers=admin,
            )
            r = client.post("/api/assistant/chat", json={"prompt": "恢复了吗"}, headers=sz)
            record("reopen restores access", r.status_code == 200, f"HTTP {r.status_code}")

            # Tool level.
            client.put(
                "/api/assistant/admin/switches",
                json={"scope": "tool", "scope_ref": "sop_draft", "enabled": False,
                      "reason": "SOP 模板改版"},
                headers=admin,
            )
            tools = client.get("/api/assistant/tools", headers=sz).json()
            invoke = client.post("/api/assistant/tools/sop_draft/invoke",
                                 json={"arguments": {}}, headers=sz)
            record(
                "tool level removes + 409",
                "sop_draft" not in tools["tools"] and invoke.status_code == 409,
                f"tools={len(tools['tools'])} blocked={tools['blocked_tools']} "
                f"invoke={invoke.status_code}",
            )
            client.put(
                "/api/assistant/admin/switches",
                json={"scope": "tool", "scope_ref": "sop_draft", "enabled": True,
                      "reason": "改版完成"},
                headers=admin,
            )

            # Admin guard.
            forbidden = client.get(
                "/api/assistant/admin/switches",
                headers={"Authorization": "Bearer tok-manager-lin"},
            )
            record(
                "non-admin -> 403 SWITCH_FORBIDDEN",
                forbidden.status_code == 403
                and forbidden.json()["code"] == "SWITCH_FORBIDDEN",
                f"HTTP {forbidden.status_code}",
            )
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        database.dispose()

    # AC1 runs against a dead database, so it gets its own app instance.
    from fastapi.testclient import TestClient

    dead = Database(Settings(database_url=(
        "postgresql+psycopg://nobody:nobody@127.0.0.1:59999/no_such_database"), cache_ttl_seconds=5.0))
    dead_app = create_app(
        Settings(database_url="postgresql+psycopg://nobody:nobody@127.0.0.1:59999/x", cache_ttl_seconds=5.0),
        database=dead,
        cache=InMemorySwitchCache("asst", CacheStats(), 5.0),
        environ={"ASST_FORCE_OFF": "true"},
    )
    with TestClient(dead_app) as client:
        r = client.post("/api/assistant/chat", json={"prompt": "断库后还能用吗"}, headers=hz)
        status = client.get("/api/assistant/status", headers=hz)
    record(
        "AC1 force-off with dead database",
        r.status_code == 503 and r.json()["code"] == "ASSISTANT_DISABLED"
        and dead.stats.total() == 0 and status.status_code == 200,
        f"chat={r.status_code} code={r.json()['code']} db_ops={dead.stats.snapshot()} "
        f"status={status.status_code} enabled={status.json()['enabled']}",
    )
    dead.dispose()

    ok = all(bool(item["ok"]) for item in RESULTS)
    print("SMOKE_RESULT=" + ("PASS" if ok else "FAIL") + f" checks={len(RESULTS)}")
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
