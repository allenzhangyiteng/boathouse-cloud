"""Account boundaries and the one-time credentials used by customer onboarding."""
import os
import html
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PG_PASSWORD="x",
                  BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-auth-security-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest
from fastapi.testclient import TestClient

from app import auth, config, db, front, mail, main

PW = "correct horse battery"
OTHER_PW = "a completely different password"
VICTIM = "customer@example.test"
OWNER = "other-owner@example.test"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for name, value in {"STATE_DIR": tmp_path, "DB_PATH": tmp_path / "state.db",
                        "MASTER_KEY_PATH": tmp_path / "master.key", "METER": False}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(auth, "_attempts", {})
    monkeypatch.setattr(mail, "configured", lambda: True)
    delivered = []
    monkeypatch.setattr(mail, "send", lambda *args, **kwargs: delivered.append((args, kwargs)) or True)
    db.init()
    auth.master_key()
    with db.conn() as c:
        for wid, slug, email in [("ws_private", "private-work", VICTIM), ("ws_other", "other-work", OWNER)]:
            c.execute("INSERT INTO workspaces (id, slug, name, created) VALUES (?,?,?,?)", (wid, slug, slug, time.time()))
            c.execute("INSERT INTO users (id, workspace_id, email, role, created) VALUES (?,?,?,?,?)",
                      (db.new_id("u"), wid, email, "owner", time.time()))
    auth.set_account_password(VICTIM, PW)
    auth.set_account_password(OWNER, PW)
    key = auth.create_project_key("ws_other", OWNER, "owner agent")
    with TestClient(main.app, base_url=f"https://{config.PLATFORM_DOMAIN}", follow_redirects=False) as client:
        yield client, {"host": config.API_HOST, "Authorization": f"Bearer {key}"}, delivered


def csrf(response):
    return re.search(r'name=csrf value="([^"]+)"', response.text)[1]


def test_owner_cannot_reset_another_accounts_password(setup):
    client, headers, delivered = setup
    assert client.post("/api/users", headers=headers, json={"email": VICTIM}).status_code == 200
    response = client.post(f"/api/users/{VICTIM}/invite", headers=headers)
    assert response.status_code == 200
    assert response.json()["invite_url"] is None
    assert response.json()["sign_in_url"].startswith(f"https://{config.PLATFORM_DOMAIN}/login")
    assert auth.check_login(VICTIM, PW)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM invites WHERE email=? AND used IS NULL", (VICTIM,)).fetchone()[0] == 0


def test_stale_invitation_cannot_overwrite_a_later_account_password(setup):
    client, headers, _ = setup
    new_email = "new-customer@example.test"
    response = client.post("/api/users", headers=headers, json={"email": new_email})
    token = response.json()["invite_url"].rsplit("/", 1)[1]
    invitation = auth.invite_for(token)
    proof = auth.invite_proof(token)
    auth.set_account_password(new_email, PW)
    assert auth.accept_invite(invitation, OTHER_PW, "Impostor", proof) is False
    assert auth.check_login(new_email, PW)
    response = client.post(f"/join/{token}", data={"csrf": auth.csrf_token("join"), "password": OTHER_PW, "password2": OTHER_PW})
    assert response.status_code == 303 and "/login" in response.headers["location"]
    assert "bh_account" not in response.cookies


def test_claim_can_only_be_redeemed_once_under_concurrency(setup, monkeypatch):
    key = auth.create_project_key(auth.ACCOUNT, VICTIM, "customer agent")
    code = auth.create_claim("ws_private", VICTIM, key)
    start = Barrier(8)
    # Force every caller through the old read-before-write window if it returns.
    # The guarded UPDATE uses no such SELECT and does not enter this barrier.
    reads = Barrier(8)
    original_conn = db.conn
    @contextmanager
    def concurrent_reads():
        with original_conn() as connection:
            class Connection:
                def execute(self, sql, parameters=()):
                    result = connection.execute(sql, parameters)
                    if sql == "SELECT * FROM claims WHERE code=?":
                        row = result.fetchone()
                        reads.wait(timeout=5)
                        class Row:
                            def fetchone(self):
                                return row
                        return Row()
                    return result
            yield Connection()
    monkeypatch.setattr(db, "conn", concurrent_reads)
    def redeem(_):
        start.wait()
        return auth.redeem_claim(code)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(redeem, range(8)))
    assert sum(result is not None for result in results) == 1


def test_cookie_authenticated_api_rejects_sibling_tool_requests(setup):
    client, _, _ = setup
    client.cookies.set(config.SESSION_COOKIE, auth.create_session("ws_other", OWNER, None))
    response = client.post("/api/users", headers={"host": f"auth.other-work.{config.PLATFORM_DOMAIN}",
                                                "Origin": f"https://evil.other-work.{config.PLATFORM_DOMAIN}"},
                           json={"email": "intruder@example.test", "role": "owner"})
    assert response.status_code == 403
    assert main._user("ws_other", "intruder@example.test") is None


def test_platform_redirect_rejects_browser_backslash_normalization():
    assert front._safe_next("/\\evil.example/path") == "/account"
    assert front._safe_next("/\n/evil.example/path") == "/account"


def test_malformed_signed_token_is_rejected_without_server_error(setup):
    assert auth.verify("x.é") is None


def test_recovery_email_proves_ownership_and_revokes_prior_access(setup):
    client, _, delivered = setup
    platform_session = auth.create_platform_session(VICTIM)
    tenant_session = auth.create_session("ws_private", VICTIM, None)
    key = auth.create_project_key(auth.ACCOUNT, VICTIM, "existing agent")
    claim = auth.create_claim("ws_private", VICTIM, key)
    handoff = auth.sign({"handoff": "ws_private", "email": VICTIM, "n": "before-recovery", "exp": time.time() + 60,
                         "pw": auth.credential_stamp(VICTIM)})
    response = client.post("/forgot-password", data={"email": VICTIM, "csrf": csrf(client.get("/forgot-password"))})
    assert response.status_code == 200 and "If that email has a Boat House account" in response.text
    assert len(delivered) == 1 and delivered[0][0][0] == VICTIM
    reset_url = re.search(r"https://\S+/reset-password/\S+", delivered[0][0][2])[0]
    token = reset_url.rsplit("/", 1)[1]
    assert token not in response.text
    another_token = auth.create_password_reset(VICTIM)
    page = client.get(reset_url)
    assert page.status_code == 200 and page.headers["Referrer-Policy"] == "strict-origin"
    assert page.headers["Cache-Control"] == "no-store"
    response = client.post(reset_url, data={"csrf": csrf(page), "password": OTHER_PW, "password2": OTHER_PW})
    assert response.status_code == 303 and response.headers["location"] == "/account"
    assert auth.check_login(VICTIM, OTHER_PW) and not auth.check_login(VICTIM, PW)
    assert auth.platform_email(platform_session) is None
    assert auth.session_user(tenant_session) is None
    assert auth.key_user(key) is None and auth.redeem_claim(claim) is None
    assert auth.password_reset_for(another_token) is None
    assert client.get(reset_url).status_code == 410
    assert client.get(f"/handoff?t={handoff}", headers={"host": f"auth.private-work.{config.PLATFORM_DOMAIN}"}).status_code == 404
    assert auth.platform_email(response.cookies[config.PLATFORM_COOKIE]) == VICTIM


def test_recovery_form_does_not_reveal_accounts_or_tokens(setup):
    client, _, delivered = setup
    token = csrf(client.get("/forgot-password"))
    known = client.post("/forgot-password", data={"email": VICTIM, "csrf": token})
    unknown = client.post("/forgot-password", data={"email": "not-registered@example.test", "csrf": token})
    assert known.status_code == unknown.status_code == 200
    # CSRF expiry timestamps vary per render; the customer-facing content agrees.
    assert known.text == unknown.text
    assert len(delivered) == 1
    assert client.post("/forgot-password", data={"email": VICTIM, "csrf": "bad"}).status_code == 400
    assert len(delivered) == 1


def test_recovery_unavailable_mail_has_a_clear_error_without_disclosing_a_link(setup, monkeypatch):
    client, _, delivered = setup
    monkeypatch.setattr(mail, "configured", lambda: False)
    response = client.post("/forgot-password", data={"email": VICTIM, "csrf": csrf(client.get("/forgot-password"))})
    assert response.status_code == 503 and "temporarily unavailable" in response.text
    assert "/reset-password/" not in response.text and delivered == []


def test_recovery_expired_token_and_invalid_password_do_not_change_credentials(setup):
    client, _, _ = setup
    expired = auth.sign({"reset": VICTIM, "n": "old", "pw": auth.credential_stamp(VICTIM), "exp": time.time() - 1})
    assert client.get(f"/reset-password/{expired}").status_code == 410
    token = auth.create_password_reset(VICTIM)
    page = client.get(f"/reset-password/{token}")
    response = client.post(f"/reset-password/{token}", data={"csrf": csrf(page), "password": "short", "password2": "short"})
    assert response.status_code == 400 and auth.check_login(VICTIM, PW)
    assert auth.password_reset_for(token)


def test_password_reset_is_atomic_across_different_tokens(setup):
    tokens = [auth.create_password_reset(VICTIM) for _ in range(6)]
    start = Barrier(6)
    def reset(token):
        start.wait()
        return auth.reset_password(token, OTHER_PW)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(reset, tokens))
    assert results.count(VICTIM) == 1


def test_invites_in_different_workspaces_can_only_initialize_one_account(setup):
    email = "waiting@example.test"
    with db.conn() as c:
        for workspace in ("ws_private", "ws_other"):
            c.execute("INSERT INTO users (id, workspace_id, email, role, created) VALUES (?,?,?,?,?)",
                      (db.new_id("u"), workspace, email, "member", time.time()))
    tokens = [auth.create_invite(workspace, email, OWNER) for workspace in ("ws_private", "ws_other")]
    invitations = [(auth.invite_for(token), auth.invite_proof(token)) for token in tokens]
    start = Barrier(2)
    def accept(item):
        start.wait()
        return auth.accept_invite(item[0], PW, "Customer", item[1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, invitations))
    assert results.count(True) == 1
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM invites WHERE email=? AND used IS NULL", (email,)).fetchone()[0] == 0


def test_handoff_token_redemption_is_atomic(setup):
    token = auth.sign({"exp": time.time() + 60, "n": "handoff"})
    start = Barrier(8)
    def redeem(_):
        start.wait()
        return auth.spend_token(token, time.time() + 60)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(redeem, range(8))).count(True) == 1


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example", "https://evil.other-work.platform.test"])
def test_cookie_api_requires_a_trusted_origin(setup, origin):
    client, _, _ = setup
    client.cookies.set(config.SESSION_COOKIE, auth.create_session("ws_other", OWNER, None))
    headers = {"host": f"auth.other-work.{config.PLATFORM_DOMAIN}"}
    if origin:
        headers["Origin"] = origin
    assert client.post("/api/users", headers=headers, json={"email": "intruder@example.test"}).status_code == 403


def test_cookie_api_accepts_its_own_origin_and_login_rejects_foreign_origin(setup):
    client, _, _ = setup
    client.cookies.set(config.SESSION_COOKIE, auth.create_session("ws_other", OWNER, None))
    host = f"auth.other-work.{config.PLATFORM_DOMAIN}"
    assert client.post("/api/users", headers={"host": host, "Origin": f"https://{host}"}, json={"email": "new@example.test"}).status_code == 200
    client.cookies.clear()
    response = client.post("/login", headers={"Origin": "https://evil.example"},
                           data={"csrf": auth.csrf_token("plogin"), "email": OWNER, "password": PW})
    assert response.status_code == 403 and config.PLATFORM_COOKIE not in response.cookies


def test_revoked_key_and_removed_membership_invalidate_unredeemed_claims(setup):
    key = auth.create_project_key(auth.ACCOUNT, VICTIM, "agent")
    code = auth.create_claim("ws_private", VICTIM, key)
    with db.conn() as c:
        c.execute("DELETE FROM project_keys WHERE email=?", (VICTIM,))
    assert auth.redeem_claim(code) is None
    key = auth.create_project_key(auth.ACCOUNT, VICTIM, "agent")
    code = auth.create_claim("ws_private", VICTIM, key)
    with db.conn() as c:
        c.execute("DELETE FROM users WHERE email=?", (VICTIM,))
    assert auth.redeem_claim(code) is None


def test_workspace_creation_cannot_overwrite_a_preexisting_password(setup):
    with pytest.raises(main.HTTPException) as error:
        main.create_workspace("Another customer workspace", VICTIM, password=OTHER_PW)
    assert error.value.status_code == 409
    assert auth.check_login(VICTIM, PW)
    assert db.workspace("another-customer-workspa") is None


def test_me_on_nonworkspace_host_returns_404_instead_of_crashing(setup):
    client, _, _ = setup
    client.cookies.set(config.SESSION_COOKIE, auth.create_session("ws_private", VICTIM, None))
    assert client.get("/me").status_code == 404


def test_signup_cannot_claim_pending_memberships_without_the_mailbox(setup):
    client, _, delivered = setup
    email = "pending@example.test"
    with db.conn() as c:
        c.execute("INSERT INTO users (id, workspace_id, email, role, created) VALUES (?,?,?,?,?)",
                  (db.new_id("u"), "ws_private", email, "member", time.time()))
    response = client.post("/signup", data={"csrf": csrf(client.get("/signup")), "workspace": "Customer studio", "email": email,
                                            "password": "attacker selected password"})
    assert response.status_code == 202
    assert not auth.has_password(email) and db.workspace("customer-studio") is None
    assert config.PLATFORM_COOKIE not in response.cookies
    link = re.search(r"https://\S+/verify-email/\S+", delivered[-1][0][2])[0]
    token = link.rsplit("/", 1)[1]
    assert token not in response.text and "password" not in auth.signup_details(token)
    page = client.get(link)
    assert page.status_code == 200 and not auth.has_password(email)  # mail scanners cannot activate it
    response = client.post(link, data={"csrf": csrf(page), "password": PW, "password2": PW})
    assert response.status_code == 303 and auth.check_login(email, PW)
    assert not auth.check_login(email, "attacker selected password")
    assert auth.signup_details(token) is None
    assert client.get(link).status_code == 410
    assert {w["slug"] for w in auth.memberships(email)} == {"customer-studio", "private-work"}


def test_owner_visible_invite_is_not_proof_of_a_new_accounts_mailbox(setup):
    client, headers, delivered = setup
    email = "pending@example.test"
    with db.conn() as c:
        c.execute("INSERT INTO users (id, workspace_id, email, role, created) VALUES (?,?,?,?,?)",
                  (db.new_id("u"), "ws_private", email, "member", time.time()))
    response = client.post("/api/users", headers=headers, json={"email": email})
    public_link = response.json()["invite_url"]
    assert "verify=" not in public_link and "verify=" not in response.text
    private_link = re.search(r"https://\S+/join/\S+", delivered[-1][0][2])[0]
    assert "verify=" in private_link
    page = client.get(public_link)
    assert "name=password" not in page.text
    response = client.post(public_link, data={"csrf": csrf(page), "password": OTHER_PW, "password2": OTHER_PW})
    assert response.status_code == 200 and not auth.has_password(email)
    assert config.PLATFORM_COOKIE not in response.cookies
    page = client.get(private_link)
    response = client.post(private_link, data={"csrf": csrf(page), "password": PW, "password2": PW})
    assert response.status_code == 303 and auth.check_login(email, PW)
    assert not auth.check_login(email, OTHER_PW)


def test_invite_proof_cannot_be_reused_for_another_invitation(setup):
    client, headers, _ = setup
    one = client.post("/api/users", headers=headers, json={"email": "one@example.test"}).json()["invite_url"].rsplit("/", 1)[1]
    two = client.post("/api/users", headers=headers, json={"email": "two@example.test"}).json()["invite_url"].rsplit("/", 1)[1]
    proof = auth.invite_proof(one)
    assert not auth.accept_invite(auth.invite_for(two), PW, None, proof)
    assert not auth.has_password("two@example.test")


def test_api_signup_sends_only_the_mailbox_a_credential(setup):
    client, _, delivered = setup
    response = client.post("/api/signup", headers={"host": config.API_HOST}, json={"workspace": "New studio", "email": "fresh@example.test"})
    assert response.status_code == 200 and response.json()["emailed"] is True
    assert not auth.has_password("fresh@example.test")
    assert "verify=" not in response.text and "verify=" in delivered[-1][0][2]
    assert delivered[-1][1]["reply_to"] is None


def test_signup_mail_failure_does_not_create_or_authenticate_the_account(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(mail, "send", lambda *a, **kw: False)
    response = client.post("/signup", data={"csrf": csrf(client.get("/signup")), "workspace": "Mail down", "email": "fresh@example.test"})
    assert response.status_code == 503 and "could not send" in response.text
    assert not auth.has_password("fresh@example.test") and db.workspace("mail-down") is None
    assert config.PLATFORM_COOKIE not in response.cookies


def test_signup_confirmation_checks_csrf_expiry_and_replay(setup):
    client, _, _ = setup
    token = auth.signup_token("Secret studio", "fresh@example.test", "")
    response = client.post(f"/verify-email/{token}", data={"csrf": "bad", "password": PW, "password2": PW})
    assert response.status_code == 400 and not auth.has_password("fresh@example.test")
    expired = auth.encrypt('{"signup":true,"workspace":"Secret studio","email":"fresh@example.test","code":"","exp":1}')
    assert client.get(f"/verify-email/{expired}").status_code == 410
    assert client.get("/verify-email/invalid").status_code == 410


@pytest.mark.parametrize("kind", ["signup", "invite", "recovery"])
def test_token_forms_preserve_browser_origin_without_leaking_token_referrers(setup, kind):
    client, headers, _ = setup
    email = "browser-customer@example.test"
    if kind == "signup":
        link = "/verify-email/" + auth.signup_token("Browser studio", email, "")
    elif kind == "invite":
        public_link = client.post("/api/users", headers=headers, json={"email": email}).json()["invite_url"]
        token = public_link.rsplit("/", 1)[1]
        link = public_link + "?verify=" + auth.invite_proof(token)
    else:
        link = "/reset-password/" + auth.create_password_reset(VICTIM)
    page = client.get(link)
    assert page.status_code == 200 and page.headers["Cache-Control"] == "no-store"
    # WHATWG Fetch's append-Origin algorithm gives HTML form navigation POSTs
    # Origin:null under no-referrer. strict-origin retains the HTTPS origin and
    # removes token-bearing paths/queries from both local and external referrers.
    assert page.headers["Referrer-Policy"] == "strict-origin"
    values = {"csrf": csrf(page), "password": OTHER_PW, "password2": OTHER_PW}
    assert client.post(link, data=values, headers={"Origin": "null"}).status_code == 403
    response = client.post(link, data=values, headers={"Origin": f"https://{config.PLATFORM_DOMAIN}"})
    assert response.status_code == 303 and config.PLATFORM_COOKIE in response.cookies


def test_existing_account_signup_retains_workspace_after_sign_in(setup):
    client, _, _ = setup
    workspace = "Customer & Team"
    response = client.post("/signup", data={"csrf": csrf(client.get("/signup")), "workspace": workspace, "email": VICTIM})
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    next_url = html.unescape(re.search(r'name=next value="([^"]+)"', page.text)[1])
    response = client.post("/login", data={"csrf": csrf(page), "email": VICTIM, "password": PW, "next": next_url},
                           headers={"Origin": f"https://{config.PLATFORM_DOMAIN}"})
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert page.status_code == 200 and 'name=workspace value="Customer &amp; Team"' in page.text
    assert 'action="/account/new-workspace"' in page.text
    response = client.post("/account/new-workspace", data={"csrf": csrf(page), "workspace": workspace},
                           headers={"Origin": f"https://{config.PLATFORM_DOMAIN}"})
    assert response.status_code == 303 and db.workspace("customer-team")


def test_signup_return_preserves_referral_and_safely_encodes_names(setup):
    client, _, _ = setup
    response = client.post("/signup", data={"csrf": csrf(client.get("/signup")), "workspace": 'Name & "Team"',
                                            "email": VICTIM, "code": "A-CODE"})
    page = client.get(response.headers["location"])
    next_url = html.unescape(re.search(r'name=next value="([^"]+)"', page.text)[1])
    client.cookies.set(config.PLATFORM_COOKIE, auth.create_platform_session(VICTIM))
    page = client.get(next_url)
    assert 'value="Name &amp; &quot;Team&quot;"' in page.text and 'name=code value="A-CODE"' in page.text


def test_expired_invitation_can_email_a_new_secret_without_owner_assistance(setup):
    client, headers, delivered = setup
    email = "expired@example.test"
    public_link = client.post("/api/users", headers=headers, json={"email": email}).json()["invite_url"]
    with db.conn() as c:
        c.execute("UPDATE invites SET expires=? WHERE email=?", (time.time() - 1, email))
    page = client.get(public_link)
    assert page.status_code == 200 and "invitation expired" in page.text
    delivered.clear()
    response = client.post(public_link, data={"csrf": csrf(page)}, headers={"Origin": f"https://{config.PLATFORM_DOMAIN}"})
    assert response.status_code == 200 and "Check your inbox" in response.text
    assert not auth.has_password(email) and config.PLATFORM_COOKIE not in response.cookies
    secure_link = re.search(r"https://\S+/join/\S+", delivered[0][0][2])[0]
    assert "verify=" in secure_link and secure_link not in response.text
    page = client.get(secure_link)
    response = client.post(secure_link, data={"csrf": csrf(page), "password": PW, "password2": PW},
                           headers={"Origin": f"https://{config.PLATFORM_DOMAIN}"})
    assert response.status_code == 303 and auth.check_login(email, PW)
    # The welcome link remains useful on a later visit; it cannot issue a new session.
    client.cookies.clear()
    response = client.get(secure_link)
    assert response.status_code == 303 and "/login?next=" in response.headers["location"]
    assert config.PLATFORM_COOKIE not in response.cookies


def test_unknown_or_removed_invitation_offers_sign_in_without_sending_email(setup):
    client, headers, delivered = setup
    link = client.post("/api/users", headers=headers, json={"email": "removed@example.test"}).json()["invite_url"]
    with db.conn() as c:
        c.execute("DELETE FROM users WHERE email='removed@example.test'")
    delivered.clear()
    for path in (link, "/join/not-a-real-token"):
        response = client.get(path)
        assert response.status_code == 404 and 'href="/login"' in response.text and 'href="/forgot-password"' in response.text
        response = client.post(path, data={"csrf": auth.csrf_token("join")})
        assert response.status_code == 404
    assert delivered == []
