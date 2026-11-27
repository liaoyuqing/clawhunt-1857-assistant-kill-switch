"""S2-IAM stub: token -> caller with roles, sites and the system-admin flag.

The real identity service issues the tokens; here a fixed directory of fictional
accounts stands in. Every name is invented for this delivery.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.switch_domain import CallerContext


@dataclass(frozen=True)
class Principal:
    user_id: str
    display_name: str
    org_id: str
    roles: tuple[str, ...] = ()
    site_ids: tuple[str, ...] = ()
    is_system_admin: bool = False

    def caller(self, tool: str | None = None) -> CallerContext:
        return CallerContext(
            user_id=self.user_id,
            org_id=self.org_id,
            roles=self.roles,
            site_ids=self.site_ids,
            tool=tool,
        )


@dataclass
class IamDirectory:
    """Bearer token -> principal. Tokens are fake strings, not secrets."""

    principals: dict[str, Principal] = field(default_factory=dict)

    def authenticate(self, token: str) -> Principal | None:
        return self.principals.get(token)

    def register(self, token: str, principal: Principal) -> None:
        self.principals[token] = principal


DEFAULT_TOKENS: dict[str, Principal] = {
    "tok-admin-zhou": Principal(
        user_id="u-1001",
        display_name="周敏（系统管理员）",
        org_id="org-xinglan",
        roles=("platform_admin",),
        site_ids=("site-hangzhou",),
        is_system_admin=True,
    ),
    "tok-manager-lin": Principal(
        user_id="u-1002",
        display_name="林澈（站点管理员）",
        org_id="org-xinglan",
        roles=("site_manager",),
        site_ids=("site-hangzhou",),
    ),
    "tok-staff-hangzhou": Principal(
        user_id="u-1003",
        display_name="何然（杭州站点运营）",
        org_id="org-xinglan",
        roles=("operation",),
        site_ids=("site-hangzhou",),
    ),
    "tok-staff-suzhou": Principal(
        user_id="u-1004",
        display_name="苏禾（苏州站点运营）",
        org_id="org-xinglan",
        roles=("operation",),
        site_ids=("site-suzhou",),
    ),
    "tok-staff-dual": Principal(
        user_id="u-1005",
        display_name="唐宁（双站点轮岗）",
        org_id="org-xinglan",
        roles=("operation", "site_manager"),
        site_ids=("site-hangzhou", "site-suzhou"),
    ),
}


def default_directory() -> IamDirectory:
    directory = IamDirectory()
    for token, principal in DEFAULT_TOKENS.items():
        directory.register(token, principal)
    return directory
