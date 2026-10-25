"""Accounts, sessions, rate limits, administration and project membership rules (M09)."""

import hashlib

import pytest
from httpx import AsyncClient

from app.cli import create_user as cli_create_user
from tests.conftest import TEST_PASSWORD, Account, StartApp, create_user, login
from tests.test_runs_api import conversation_with_question, db_execute

COOKIE = "bioresearch_session"


async def test_login_sets_a_hardened_session_cookie_and_me_identifies_the_user(
    start_app: StartApp, user: Account
) -> None:
    async with start_app(as_user=None) as client:
        response = await client.post(
            "/api/v1/auth/login", json={"email": "  ALICE@example.org ", "password": TEST_PASSWORD}
        )
        me = await client.get("/api/v1/auth/me")

    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie and "secure" not in cookie
    assert me.json()["email"] == "alice@example.org" and me.json()["id"] == str(user.id)
    assert "password_hash" not in me.json()


async def test_secure_cookie_flag_follows_the_setting(start_app: StartApp, user: Account) -> None:
    async with start_app(as_user=None, session_cookie_secure=True) as client:
        response = await client.post("/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert "secure" in response.headers["set-cookie"].lower()


@pytest.mark.parametrize(
    ("email", "password"), [("alice@example.org", "wrong password!!"), ("nobody@example.org", TEST_PASSWORD)]
)
async def test_wrong_password_and_unknown_email_fail_identically(
    start_app: StartApp, user: Account, email: str, password: str
) -> None:
    async with start_app(as_user=None) as client:
        response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert response.json()["error"]["message"] == "Invalid email or password"
    assert "set-cookie" not in response.headers


async def test_only_a_hash_of_the_session_token_is_stored(client: AsyncClient, migrated_database: str) -> None:
    token = client.cookies[COOKIE]
    stored = [row[0] for row in db_execute(migrated_database, "SELECT token_sha256 FROM user_sessions")]
    assert stored == [hashlib.sha256(token.encode()).hexdigest()]
    passwords = db_execute(migrated_database, "SELECT password_hash FROM users")
    assert passwords[0][0].startswith("$argon2id$")


async def test_logout_revokes_the_session_server_side(client: AsyncClient) -> None:
    token = client.cookies[COOKIE]
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    client.cookies.set(COOKIE, token)  # a stolen copy of the cookie is useless after logout
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE user_sessions SET expires_at = now() - interval '1 minute'",
        "UPDATE user_sessions SET last_seen_at = now() - interval '73 hours'",
        "UPDATE users SET disabled = true",
    ],
    ids=["expired", "idle", "disabled-user"],
)
async def test_expired_idle_and_disabled_sessions_are_rejected(
    client: AsyncClient, migrated_database: str, sql: str
) -> None:
    db_execute(migrated_database, sql)
    response = await client.get("/api/v1/projects")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_password_change_signs_out_other_sessions_only(start_app: StartApp, user: Account) -> None:
    async with start_app() as laptop, start_app() as phone:
        changed = await laptop.post(
            "/api/v1/auth/password",
            json={"current_password": TEST_PASSWORD, "new_password": "a much longer new passphrase"},
        )
        wrong = await laptop.post(
            "/api/v1/auth/password", json={"current_password": "not it at all", "new_password": "x" * 12}
        )
        assert changed.status_code == 204
        assert wrong.status_code == 401
        assert (await laptop.get("/api/v1/auth/me")).status_code == 200
        assert (await phone.get("/api/v1/auth/me")).status_code == 401
        old = await phone.post("/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
        assert old.status_code == 401


async def test_short_new_password_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "short"}
    )
    assert response.status_code == 422


async def test_login_attempts_are_rate_limited_per_email(start_app: StartApp, user: Account) -> None:
    async with start_app(as_user=None, rate_limit_login_per_minute=3) as client:
        statuses = [
            (
                await client.post("/api/v1/auth/login", json={"email": user.email, "password": "guess number x"})
            ).status_code
            for _ in range(3)
        ]
        blocked = await client.post("/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert statuses == [401, 401, 401]
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1


async def test_model_calls_are_rate_limited_per_user(start_app: StartApp, migrated_database: str) -> None:
    async with start_app(rate_limit_model_per_minute=1) as alice:
        conversation_id = await conversation_with_question(alice)
        first = await alice.post(f"/api/v1/chat/{conversation_id}/runs", json={})
        second = await alice.post(f"/api/v1/chat/{conversation_id}/runs", json={})
        bob = await create_user(migrated_database, "bob@example.org")
        await alice.post("/api/v1/auth/logout")
        await login(alice, bob)
        other_user = await alice.post(
            "/api/v1/retrieval/query", json={"project_id": conversation_id, "query": "x", "mode": "keyword"}
        )
    assert first.status_code == 202
    assert second.status_code == 429 and second.json()["error"]["code"] == "rate_limited"
    assert other_user.status_code == 404  # Bob's own budget is untouched (and he cannot see Alice's project)


async def test_admin_manages_accounts_but_gets_no_project_access(
    client: AsyncClient, start_app: StartApp, migrated_database: str
) -> None:
    admin = await create_user(migrated_database, "root@example.org", admin=True)
    project_id = (await client.post("/api/v1/projects", json={"title": "Private"})).json()["id"]
    async with start_app(as_user=admin) as root:
        created = await root.post(
            "/api/v1/admin/users",
            json={"email": "New.Person@Example.org", "display_name": "New", "password": TEST_PASSWORD},
        )
        duplicate = await root.post(
            "/api/v1/admin/users",
            json={"email": "new.person@example.org", "display_name": "x", "password": TEST_PASSWORD},
        )
        users = [u["email"] for u in (await root.get("/api/v1/admin/users")).json()]
        self_disable = await root.patch(f"/api/v1/admin/users/{admin.id}", json={"disabled": True})
        private = await root.get(f"/api/v1/projects/{project_id}")
    assert created.status_code == 201 and created.json()["email"] == "new.person@example.org"
    assert duplicate.status_code == 409
    assert users == ["alice@example.org", "new.person@example.org", "root@example.org"]
    assert self_disable.status_code == 409
    assert private.status_code == 404
    assert (await client.get("/api/v1/admin/users")).status_code == 403


async def test_disabling_a_user_ends_their_sessions(
    client: AsyncClient, start_app: StartApp, migrated_database: str, user: Account
) -> None:
    admin = await create_user(migrated_database, "root@example.org", admin=True)
    async with start_app(as_user=admin) as root:
        disabled = await root.patch(f"/api/v1/admin/users/{user.id}", json={"disabled": True})
    assert disabled.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_membership_rules(
    client: AsyncClient, start_app: StartApp, migrated_database: str, user: Account
) -> None:
    project_id = (await client.post("/api/v1/projects", json={"title": "Team"})).json()["id"]
    bob = await create_user(migrated_database, "bob@example.org")
    url = f"/api/v1/projects/{project_id}/members"

    unknown = await client.post(url, json={"email": "ghost@example.org", "role": "viewer"})
    added = await client.post(url, json={"email": "BOB@example.org", "role": "editor"})
    again = await client.post(url, json={"email": bob.email, "role": "viewer"})
    demote_last_owner = await client.patch(f"{url}/{user.id}", json={"role": "editor"})
    leave_as_last_owner = await client.delete(f"{url}/{user.id}")
    promoted = await client.patch(f"{url}/{bob.id}", json={"role": "owner"})
    async with start_app(as_user=bob) as bob_client:
        bob_projects = (await bob_client.get("/api/v1/projects")).json()["items"]
    left = await client.delete(f"{url}/{user.id}")  # now possible: Bob is also an owner

    assert unknown.status_code == 404
    assert added.status_code == 201 and added.json()["role"] == "editor"
    assert again.status_code == 409
    assert demote_last_owner.status_code == 409 and demote_last_owner.json()["error"]["code"] == "last_owner"
    assert leave_as_last_owner.status_code == 409
    assert promoted.json()["role"] == "owner"
    assert [(p["id"], p["role"]) for p in bob_projects] == [(project_id, "owner")]
    assert left.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 404


async def test_cli_creates_the_first_admin_and_claims_projects_from_before_accounts(
    migrated_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_execute(migrated_database, "INSERT INTO projects (id, title) VALUES (gen_random_uuid(), 'Legacy')")
    monkeypatch.setenv("DATABASE_URL", migrated_database)
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        await cli_create_user("first@example.org", "First", TEST_PASSWORD, admin=True, claim=True)
    finally:
        get_settings.cache_clear()

    rows = db_execute(
        migrated_database,
        "SELECT u.is_admin, m.role, p.title FROM users u JOIN project_members m ON m.user_id = u.id "
        "JOIN projects p ON p.id = m.project_id",
    )
    assert rows == [(True, "owner", "Legacy")]
