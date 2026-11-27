"""Acceptance criterion 4: the assistant switch and the recruitment AI never collide."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.cache import cache_key_names


def test_recruitment_tasks_survive_an_assistant_shutdown(
    client: TestClient, admin_headers: dict[str, str], hangzhou_headers: dict[str, str]
) -> None:
    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "global", "scope_ref": "", "enabled": False, "reason": "助手全平台演练"},
        headers=admin_headers,
    )
    assistant = client.get("/api/assistant/status", headers=hangzhou_headers).json()
    assert assistant["enabled"] is False

    listed = client.get("/api/recruitment/ai/tasks", headers=hangzhou_headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 2
    assert listed.json()["module"] == "recruitment"

    run = client.post("/api/recruitment/ai/tasks/rt-2001/run", headers=hangzhou_headers)
    assert run.status_code == 200
    assert run.json()["status"] == "running"
    assert run.json()["result"]["leads_scored"] == 12


def test_assistant_survives_a_recruitment_shutdown(
    client: TestClient, admin_headers: dict[str, str], hangzhou_headers: dict[str, str]
) -> None:
    response = client.put(
        "/api/recruitment/ai/admin/switch",
        json={"enabled": False, "reason": "招商线索模型升级"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert client.get("/api/recruitment/ai/tasks", headers=hangzhou_headers).status_code == 503

    assistant = client.get("/api/assistant/status", headers=hangzhou_headers).json()
    assert assistant["enabled"] is True
    chat = client.post("/api/assistant/chat", json={"prompt": "助手还在吗"}, headers=hangzhou_headers)
    assert chat.status_code == 200


def test_force_off_does_not_touch_recruitment(
    client: TestClient, hangzhou_headers: dict[str, str]
) -> None:
    """ASST_FORCE_OFF is an assistant-only brake; the recruitment module keeps running."""

    app = client.app
    app.state.container.assistant.resolver.environ = {"ASST_FORCE_OFF": "true"}  # type: ignore[attr-defined]
    assistant = client.get("/api/assistant/status", headers=hangzhou_headers).json()
    assert assistant["enabled"] is False
    assert assistant["source"] == "force_off"
    assert client.get("/api/recruitment/ai/tasks", headers=hangzhou_headers).status_code == 200
    app.state.container.assistant.resolver.environ = {}  # type: ignore[attr-defined]


def test_audit_action_prefixes_differ(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "global", "scope_ref": "", "enabled": False, "reason": "演练"},
        headers=admin_headers,
    )
    client.put(
        "/api/recruitment/ai/admin/switch",
        json={"enabled": False, "reason": "演练"},
        headers=admin_headers,
    )
    assistant_audit = client.get("/api/assistant/admin/audit", headers=admin_headers).json()
    recruit_audit = client.get("/api/recruitment/ai/admin/audit", headers=admin_headers).json()
    assert all(item["action"].startswith("assistant.") for item in assistant_audit["items"])
    assert all(item["action"].startswith("recruitment.") for item in recruit_audit["items"])
    assert {item["action"] for item in assistant_audit["items"]} & {
        item["action"] for item in recruit_audit["items"]
    } == set()


def test_cache_keys_do_not_share_a_namespace(shared_redis: object) -> None:
    from app.cache import RedisSwitchCache

    asst = RedisSwitchCache(shared_redis, "asst", ttl_seconds=5)  # type: ignore[arg-type]
    recruit = RedisSwitchCache(shared_redis, "recruit", ttl_seconds=5)  # type: ignore[arg-type]
    asst_keys = cache_key_names("asst", asst.generation())
    recruit_keys = cache_key_names("recruit", recruit.generation())
    assert set(asst_keys.values()).isdisjoint(set(recruit_keys.values()))
    assert asst_keys["generation"] == "asst:switch:gen"
    assert recruit_keys["generation"] == "recruit:switch:gen"
    assert recruit.generation() == 1, "a separate module must not inherit a bumped generation"
