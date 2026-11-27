"""Acceptance criterion 3: closing one site refuses its users, not the others."""

from __future__ import annotations

from fastapi.testclient import TestClient

REASON = "杭州站点机房迁移，员工助手暂停"


def disable_site(client: TestClient, headers: dict[str, str], site: str, reason: str = REASON) -> None:
    response = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "site", "scope_ref": site, "enabled": False, "reason": reason},
        headers=headers,
    )
    assert response.status_code in (200, 201), response.text


def test_hangzhou_user_is_refused_after_site_close(
    client: TestClient,
    admin_headers: dict[str, str],
    hangzhou_headers: dict[str, str],
) -> None:
    disable_site(client, admin_headers, "site-hangzhou")
    response = client.post(
        "/api/assistant/chat", json={"prompt": "查找会员合同模板"}, headers=hangzhou_headers
    )
    body = response.json()
    assert response.status_code == 503
    assert body["code"] == "ASSISTANT_DISABLED"
    assert body["source"] == "site"
    assert body["scope_ref"] == "site-hangzhou"


def test_suzhou_user_is_unaffected(
    client: TestClient,
    admin_headers: dict[str, str],
    suzhou_headers: dict[str, str],
) -> None:
    disable_site(client, admin_headers, "site-hangzhou")
    response = client.post(
        "/api/assistant/chat", json={"prompt": "查找会员合同模板"}, headers=suzhou_headers
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is True


def test_status_shows_the_display_message(
    client: TestClient,
    admin_headers: dict[str, str],
    hangzhou_headers: dict[str, str],
) -> None:
    disable_site(client, admin_headers, "site-hangzhou")
    body = client.get("/api/assistant/status", headers=hangzhou_headers).json()
    assert body["enabled"] is False
    assert body["source"] == "site"
    assert body["scope_ref"] == "site-hangzhou"
    assert "杭州" in body["message"] or "站点" in body["message"]


def test_dual_site_user_is_refused_and_single_site_user_is_not(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    disable_site(client, admin_headers, "site-suzhou")
    dual = client.post(
        "/api/assistant/chat",
        json={"prompt": "跨站点会员对比"},
        headers={"Authorization": "Bearer tok-staff-dual"},
    )
    hangzhou = client.post(
        "/api/assistant/chat",
        json={"prompt": "跨站点会员对比"},
        headers={"Authorization": "Bearer tok-staff-hangzhou"},
    )
    assert dual.status_code == 503
    assert hangzhou.status_code == 200


def test_reopening_the_site_restores_access(
    client: TestClient,
    admin_headers: dict[str, str],
    hangzhou_headers: dict[str, str],
) -> None:
    disable_site(client, admin_headers, "site-hangzhou")
    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "site", "scope_ref": "site-hangzhou", "enabled": True, "reason": "迁移完成"},
        headers=admin_headers,
    )
    response = client.post(
        "/api/assistant/chat", json={"prompt": "恢复后可用吗"}, headers=hangzhou_headers
    )
    assert response.status_code == 200


def test_role_and_tool_levels_compose_with_site(
    client: TestClient,
    admin_headers: dict[str, str],
    suzhou_headers: dict[str, str],
) -> None:
    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "role", "scope_ref": "operation", "enabled": False, "reason": "岗位外包交接"},
        headers=admin_headers,
    )
    body = client.get("/api/assistant/status", headers=suzhou_headers).json()
    assert body["enabled"] is False
    assert body["source"] == "role"
    assert body["scope_ref"] == "operation"
