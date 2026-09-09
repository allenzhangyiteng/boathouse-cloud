"""Tests for the Boathouse MCP face (control/app/mcp.py).

Run:  <venv>/bin/python -m pytest -q tests/test_mcp.py

Everything happens in-process: the MCP endpoint is reached through a TestClient on
the mcp.<platform> host, and the HTTP API the tools call is reached through an
ASGI-backed httpx client injected into mcp.client_factory. No network, no docker,
no registrar; a fresh BH_STATE_DIR per run.

config.py reads the environment at import time, so the environment is set first and
every hostname is read back from config afterwards: that way this file still works
when another test module in the same process imported the app before it did.
"""
import base64
import json
import os
import sys
import tempfile

import pytest

os.environ.setdefault("BH_STATE_DIR", tempfile.mkdtemp(prefix="bh-mcp-state-"))
os.environ.update(
    BH_METER="0",
    BH_PUBLIC_IP="203.0.113.9",
    BH_PG_PASSWORD="x",
)
os.environ.setdefault("BH_DOMAIN", "bhtest-mcp.com")
os.environ.setdefault("BH_OWNERS", "owner@example.com")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "control"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, config, db, hosts, main, mcp  # noqa: E402

PLATFORM = config.PLATFORM_DOMAIN
MCP_HOST = config.MCP_HOST
API_HOST = config.API_HOST
TOOL = "mcpdemo"                       # this file's own tool, so it cannot collide
NANCY, KIRA = "mcp-nancy@example.com", "mcp-kira@example.com"


@pytest.fixture(scope="session")
def client():
    with TestClient(main.app, base_url="https://testserver", follow_redirects=False) as c:
        # the tools call the API in-process instead of over the wire.
        # raise_app_exceptions=False so an exploding endpoint becomes a 500 response,
        # the way it would behind uvicorn on the box.
        mcp.client_factory = lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
            base_url="http://testserver", timeout=60.0)
        yield c


@pytest.fixture(scope="session")
def ws(client):
    return db.workspace()


@pytest.fixture(scope="session")
def home(ws):
    """The domain this workspace's tools are announced on."""
    return hosts.base_for(ws)


@pytest.fixture(scope="session")
def owner(ws):
    with db.conn() as c:
        r = c.execute("SELECT email FROM users WHERE workspace_id=? AND role='owner' ORDER BY created",
                      (ws["id"],)).fetchone()
    return r["email"]


@pytest.fixture(scope="session")
def key(client, ws, owner):
    return auth.create_project_key(ws["id"], owner, "mcp-test")


@pytest.fixture(scope="session")
def tool(client, key):
    """One tool row to share, inspect and delete. No container is ever built."""
    r = client.post("/api/tools", headers={"host": API_HOST, "Authorization": f"Bearer {key}"},
                    json={"slug": TOOL, "name": "MCP Demo"})
    assert r.status_code in (200, 409), r.text
    return TOOL


def rpc(client, method, params=None, key=None, msg_id=1, host=MCP_HOST):
    body = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        body["id"] = msg_id
    if params is not None:
        body["params"] = params
    headers = {"host": host}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return client.post("/mcp", headers=headers, json=body)


def call_tool(client, name, args=None, key=None):
    r = rpc(client, "tools/call", {"name": name, "arguments": args or {}}, key=key)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def text_of(result):
    return "\n".join(b["text"] for b in result["content"] if b["type"] == "text")


# ---- host and transport ------------------------------------------------------

def test_only_answers_on_the_mcp_host(client, home):
    for host in (API_HOST, PLATFORM, f"auth.{home}", home, f"{TOOL}.{home}"):
        r = rpc(client, "initialize", {}, host=host)
        assert r.status_code == 404, f"{host} -> {r.status_code}"
    assert rpc(client, "ping", {}).status_code == 200


def test_get_is_405_with_an_explanation(client):
    r = client.get("/mcp", headers={"host": MCP_HOST})
    assert r.status_code == 405
    assert r.headers["allow"] == "POST"
    assert "POST" in json.dumps(r.json())
    assert client.get("/mcp", headers={"host": API_HOST}).status_code == 404


def test_tool_calls_reach_the_api_host(client, key):
    """The in-process call must carry Host: api.<platform>; /api/signup exists only there."""
    import asyncio

    async def probe():
        async with mcp.client_factory() as c:
            try:
                await mcp.Api(c, f"Bearer {key}").post("/api/signup", json={})
            except mcp.ApiError as e:
                return e.text
        return "no error"

    text = asyncio.run(probe())
    assert text.startswith("422"), text  # a 404 would mean the Host header did not stick


# ---- protocol ----------------------------------------------------------------

def test_initialize_handshake(client):
    r = rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                   "clientInfo": {"name": "pytest", "version": "0"}})
    assert r.status_code == 200
    assert r.headers["mcp-protocol-version"] == "2025-03-26"
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-03-26"
    assert res["capabilities"]["tools"] == {}
    assert res["serverInfo"] == {"name": "boathouse", "version": "0.3"}
    assert "Boathouse" in res["instructions"] and "Bearer" in res["instructions"]


def test_initialized_notification_is_202(client):
    r = client.post("/mcp", headers={"host": MCP_HOST},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.content == b""


def test_ping(client):
    assert rpc(client, "ping").json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_unknown_method(client):
    err = rpc(client, "resources/list").json()["error"]
    assert err["code"] == -32601 and "resources/list" in err["message"]


def test_malformed_bodies(client):
    r = client.post("/mcp", headers={"host": MCP_HOST, "content-type": "application/json"}, content=b"{oops")
    assert r.json()["error"]["code"] == -32700
    r = client.post("/mcp", headers={"host": MCP_HOST}, json={"method": "ping", "id": 1})
    assert r.json()["error"]["code"] == -32600
    r = client.post("/mcp", headers={"host": MCP_HOST}, json="hello")
    assert r.json()["error"]["code"] == -32600
    r = client.post("/mcp", headers={"host": MCP_HOST}, json=[])
    assert r.json()["error"]["code"] == -32600


def test_batch(client, key, owner):
    r = client.post("/mcp", headers={"host": MCP_HOST, "Authorization": f"Bearer {key}"}, json=[
        {"jsonrpc": "2.0", "id": "a", "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "b", "method": "tools/list"},
        {"jsonrpc": "2.0", "id": "c", "method": "tools/call", "params": {"name": "whoami", "arguments": {}}},
    ])
    assert r.status_code == 200
    out = {m["id"]: m for m in r.json()}
    assert set(out) == {"a", "b", "c"}  # the notification gets no reply
    assert out["a"]["result"] == {}
    assert len(out["b"]["result"]["tools"]) == len(mcp.TOOLS)
    assert owner in text_of(out["c"]["result"])


# ---- the catalogue -----------------------------------------------------------

EXPECTED = {
    "whoami", "list_tools", "get_tool", "deploy", "pull", "logs", "releases", "rollback", "restart",
    "share", "unshare", "set_access", "secrets_list", "secrets_set", "secrets_delete",
    "users_list", "users_add", "users_invite", "users_remove",
    "domains_list", "domain_check", "domain_buy", "billing", "prices", "topup", "card_link", "delete_tool",
    "referral", "request_access", "access_requests", "allow_request",
    "domain_attach", "domain_point", "domain_primary", "domain_dns", "domain_repoint", "domain_detach",
}


def test_tools_list_is_complete_and_well_formed(client):
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == EXPECTED
    for t in tools:
        assert len(t["description"]) > 40, t["name"]
        s = t["inputSchema"]
        assert s["type"] == "object" and isinstance(s["properties"], dict)
        assert set(s.get("required", [])) <= set(s["properties"]), t["name"]
        for prop, spec in s["properties"].items():
            assert spec.get("type"), f"{t['name']}.{prop} has no type"
            assert spec.get("description"), f"{t['name']}.{prop} has no description"


def test_tools_list_needs_no_key(client):
    assert rpc(client, "tools/list").status_code == 200


# ---- authentication ----------------------------------------------------------

def test_call_without_a_key_is_a_tool_error(client):
    res = call_tool(client, "whoami")
    assert res["isError"] is True
    assert "Authorization: Bearer" in text_of(res) and "project key" in text_of(res)


def test_unknown_tool_name(client, key):
    res = call_tool(client, "nope", key=key)
    assert res["isError"] is True and "nope" in text_of(res)


def test_bad_key(client):
    res = call_tool(client, "whoami", key="bh_not_a_real_key")
    assert res["isError"] is True and "401" in text_of(res)


# ---- the tools ---------------------------------------------------------------

def test_whoami(client, key, ws, owner, home):
    res = call_tool(client, "whoami", key=key)
    assert res["isError"] is False
    assert owner in text_of(res) and home in text_of(res)
    assert res["structuredContent"]["workspace"] == ws["slug"]


def test_list_and_get_tool(client, key, tool, home):
    res = call_tool(client, "list_tools", key=key)
    assert TOOL in text_of(res)
    assert any(t["slug"] == TOOL for t in res["structuredContent"]["items"])
    res = call_tool(client, "get_tool", {"tool": TOOL}, key=key)
    assert f"https://{TOOL}.{home}" in text_of(res)
    res = call_tool(client, "get_tool", {"tool": "ghost"}, key=key)
    assert res["isError"] is True and "no tool named ghost" in text_of(res)


def test_deploy_without_a_dockerfile(client, key):
    res = call_tool(client, "deploy", {"tool": TOOL, "files": {"app.py": "print('hi')"}}, key=key)
    assert res["isError"] is True
    assert "Dockerfile" in text_of(res) and "$PORT" in text_of(res)


def test_deploy_with_only_an_index_html_is_a_website_not_a_refusal(client, key):
    # the server adds the nginx Dockerfile itself; whatever answers next (the money gate, the builder), it is not
    # the "no Dockerfile" refusal the docs promise never happens for a plain website folder
    res = call_tool(client, "deploy", {"tool": TOOL, "files": {"index.html": "<h1>hi</h1>"}}, key=key)
    assert "no Dockerfile" not in text_of(res)


def test_users_list_never_carries_password_hashes(client, key, owner):
    auth.set_account_password(owner, "a-long-enough-password-1")
    res = call_tool(client, "users_list", key=key)
    blob = json.dumps(res)
    assert "pw_hash" not in blob and "scrypt" not in blob
    assert res["structuredContent"]["items"][0]["has_password"] in (True, False)


def test_deploy_check_speaks_before_an_upload(client, key):
    r = client.get("/api/billing/deploy-check", headers={"host": API_HOST, "Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and set(r.json()) == {"ok", "message", "code"}
    if not r.json()["ok"]:
        assert "put money on the balance" in r.json()["message"]


def test_referral_and_request_tools(client, key):
    res = call_tool(client, "referral", key=key)
    assert res["isError"] is False and "your code:" in text_of(res)
    res = call_tool(client, "access_requests", key=key)
    assert res["isError"] is False and "nobody is waiting" in text_of(res)
    res = call_tool(client, "request_access", {"target": "nope"}, key=key)
    assert res["isError"] is True and "<workspace>/<tool>" in text_of(res)


def test_deploy_refuses_escaping_paths(client, key):
    res = call_tool(client, "deploy", {"tool": TOOL, "files": {"../etc/passwd": "x", "Dockerfile": "FROM x"}}, key=key)
    assert res["isError"] is True and "suspicious path" in text_of(res)


def test_deploy_needs_files(client, key):
    res = call_tool(client, "deploy", {"tool": TOOL, "files": {}}, key=key)
    assert res["isError"] is True and "files is required" in text_of(res)


def test_pull_without_source(client, key, tool):
    res = call_tool(client, "pull", {"tool": TOOL}, key=key)
    assert res["isError"] is True and "no source" in text_of(res)


@pytest.fixture(scope="session")
def planted_release(client, key, tool):
    """A release row with a real source tar.gz, exactly as the API records one after a
    deploy. Building it for real would need docker; this is the same shape without it."""
    import io
    import tarfile
    import time
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, blob in (("Dockerfile", b"FROM python:3.12-slim\n"), ("logo.png", b"\x89PNG\x00\xff")):
            ti = tarfile.TarInfo(name)
            ti.size = len(blob)
            tf.addfile(ti, io.BytesIO(blob))
    w = db.workspace()
    t = main._tool_by_slug(w["id"], tool)
    sp = main._source_path(t, 1)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_bytes(buf.getvalue())
    with db.conn() as c:
        rid = db.new_id("r")
        c.execute("INSERT INTO releases (id, tool_id, seq, image, status, note, log, created, created_by, source)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (rid, t["id"], 1, f"bh/{tool}:1", "live", "first release", None, time.time(), "test", str(sp)))
        c.execute("UPDATE tools SET current_release_id=? WHERE id=?", (rid, t["id"]))
    return 1


def test_rollback_never_targets_a_release_that_did_not_run(client, key, tool, planted_release):
    import time as _t
    t = main._tool_by_slug(db.workspace()["id"], tool)
    with db.conn() as c:
        c.execute("INSERT INTO releases (id, tool_id, seq, image, status, note, log, created, created_by, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (db.new_id("r"), t["id"], 2, f"bh/{tool}:2", "failed", "broken", "Traceback\nboom", _t.time(), "test", None))
    res = call_tool(client, "rollback", {"tool": tool, "to": 2}, key=key)
    assert res["isError"] is True and "never ran" in text_of(res) and "#1" in text_of(res) and "nothing was changed" in text_of(res)
    res = call_tool(client, "rollback", {"tool": tool, "to": 9}, key=key)
    assert res["isError"] is True and "no release #9" in text_of(res)


def test_crash_loops_are_said_once():
    from app import deploy as d
    block = '  File "/app/app.py", line 13\n    def oops(:\n             ^\nSyntaxError: invalid syntax\n'
    out = d.collapse_repeats("tool did not open port 8080 within 60s\n--- last log lines ---\n" + block * 10)
    assert out.count("SyntaxError") == 1 and "10 times" in out and out.startswith("tool did not open port")
    assert d.cause_line(out) == "SyntaxError: invalid syntax"
    tb = "Traceback (most recent call last):\n  File x\nValueError: boom\n"
    out = d.collapse_repeats("db ready\n" + tb * 6)
    assert out.count("Traceback") == 1 and "6 times" in out and out.startswith("db ready")
    assert d.collapse_repeats("one\ntwo\nthree") == "one\ntwo\nthree"


def test_structured_payloads_speak_mcp_too():
    r = mcp._tool_result("x", {"message": "this needs `bh billing card` first", "cents": 5})
    assert r["structuredContent"]["message"] == "this needs `card_link` first" and r["structuredContent"]["cents"] == 5


def test_mcp_answers_speak_in_tool_names():
    assert mcp._speak_mcp("run bh pull notes and reapply; bh billing card first") == "call pull notes and reapply; card_link first"


def test_a_deleted_tool_keeps_its_sharing_and_secrets_for_its_return(client, key, tool, owner):
    import time as _t
    w = db.workspace()
    t = main._tool_by_slug(w["id"], tool)
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO grants (id, tool_id, email, role, created, created_by, tier, labels) VALUES (?,?,?,?,?,?,?,?)",
                  (db.new_id("g"), t["id"], "keep-me@example.com", "editor", _t.time(), owner, "editor", "manager"))
        c.execute("INSERT OR REPLACE INTO secrets (tool_id, name, value_enc, updated, updated_by) VALUES (?,?,?,?,?)",
                  (t["id"], "KEPT_KEY", auth.encrypt("v1"), _t.time(), owner))
        main._keep_tool(c, w["id"], t)
        c.execute("DELETE FROM grants WHERE tool_id=? AND email='keep-me@example.com'", (t["id"],))
        c.execute("DELETE FROM secrets WHERE tool_id=?", (t["id"],))
        got = main._revive_tool(c, w["id"], t)
        assert got["grants"] >= 1 and got["secrets"] == 1
        g = c.execute("SELECT tier, labels FROM grants WHERE tool_id=? AND email='keep-me@example.com'", (t["id"],)).fetchone()
        assert g["tier"] == "editor" and g["labels"] == "manager"
        assert auth.decrypt(c.execute("SELECT value_enc FROM secrets WHERE tool_id=? AND name='KEPT_KEY'", (t["id"],)).fetchone()["value_enc"]) == "v1"
        assert c.execute("SELECT 1 FROM tool_keepsakes WHERE workspace_id=? AND slug=?", (w["id"], tool)).fetchone() is None
        c.execute("DELETE FROM grants WHERE tool_id=? AND email='keep-me@example.com'", (t["id"],))
        c.execute("DELETE FROM secrets WHERE tool_id=?", (t["id"],))


def test_open_hands_a_person_into_a_tool_once(client, tool, owner, ws, home):
    import re as _re
    sid = auth.create_platform_session(owner)
    r = client.get(f"/open/{ws['slug']}/{tool}", headers={"host": PLATFORM}, cookies={"bh_account": sid})
    assert r.status_code == 303 and r.headers["location"].startswith(f"https://auth.{home}/handoff?t="), r.headers.get("location")
    t = _re.search(r"t=([^&]+)", r.headers["location"]).group(1)
    r2 = client.get("/handoff", params={"t": t}, headers={"host": f"auth.{home}"})
    assert r2.status_code == 303 and r2.headers["location"].startswith("https://") and "bh_session" in r2.headers.get("set-cookie", "")
    r3 = client.get("/handoff", params={"t": t}, headers={"host": f"auth.{home}"})      # the same link again: dead
    assert r3.status_code == 404
    old = auth.sign({"handoff": ws["id"], "email": owner, "to": f"https://x.{home}", "exp": 9999999999})   # no nonce: never valid
    assert client.get("/handoff", params={"t": old}, headers={"host": f"auth.{home}"}).status_code == 404
    r4 = client.get(f"/open/{ws['slug']}/nope", headers={"host": PLATFORM}, cookies={"bh_account": sid})
    assert r4.status_code == 302 and "/account" in r4.headers["location"]


def test_deploy_check_lets_a_guest_admin_change_their_tool(client, key, tool, ws):
    guest = "mcp-guest-admin@example.com"
    r = client.post("/api/users", headers={"host": API_HOST, "Authorization": f"Bearer {key}"}, json={"email": guest, "role": "guest"})
    assert r.status_code == 200, r.text
    gkey = auth.create_project_key(ws["id"], guest, "t")
    H = {"host": API_HOST, "Authorization": f"Bearer {gkey}"}
    r = client.get("/api/billing/deploy-check", headers=H)                       # a new tool: not for guests
    assert r.json()["ok"] is False and r.json()["code"] == 403 and "guest" in r.json()["message"]
    r = client.get(f"/api/billing/deploy-check?tool={tool}", headers=H)          # a tool they are not admin on: no
    assert r.json()["ok"] is False and r.json()["code"] == 403 and "admins" in r.json()["message"]
    call_tool(client, "share", {"tool": tool, "email": guest, "tier": "admin"}, key=key)
    r = client.get(f"/api/billing/deploy-check?tool={tool}", headers=H)          # admin on it: only money can say no
    assert r.json()["code"] in (None, 402), r.text
    call_tool(client, "users_remove", {"email": guest}, key=key)


def test_pull_unpacks_a_release(client, key, planted_release):
    res = call_tool(client, "pull", {"tool": TOOL}, key=key)
    assert res["isError"] is False
    sc = res["structuredContent"]
    assert sc["release"] == 1
    assert sc["files"]["Dockerfile"].startswith("FROM python")
    assert base64.b64decode(sc["binary_base64"]["logo.png"]) == b"\x89PNG\x00\xff"
    assert "Dockerfile" in text_of(res)


def test_releases_and_logs(client, key, planted_release):
    res = call_tool(client, "releases", {"tool": TOOL}, key=key)
    assert res["isError"] is False and "#1" in text_of(res)
    res = call_tool(client, "logs", {"tool": TOOL, "tail": 10}, key=key)
    text = text_of(res)
    assert text  # the log lines, or (with no docker daemon here) the API's 500 turned into words
    assert res["isError"] is False or text.startswith("500"), text


def test_share_mints_an_invite_for_a_new_person(client, key, tool, home):
    res = call_tool(client, "share", {"tool": TOOL, "email": NANCY,
                                      "tier": "editor", "labels": ["consultant"]}, key=key)
    assert res["isError"] is False
    text = text_of(res)
    assert f"{NANCY} can open https://{TOOL}.{home}" in text
    assert "as editor (change the data)" in text and "labels: consultant" in text
    assert "secure confirmation email" in text and f"https://{PLATFORM}/join/inv_" in text
    assert res["structuredContent"]["invite_url"].startswith(f"https://{PLATFORM}/join/")
    # re-sharing at another tier: the tier changes, and a link is still offered (no password yet)
    res = call_tool(client, "share", {"tool": TOOL, "email": NANCY, "tier": "viewer"}, key=key)
    assert res["isError"] is False and "as viewer (look)" in text_of(res)
    assert res["structuredContent"]["tier"] == "viewer"


def test_unshare_and_access(client, key, tool):
    res = call_tool(client, "unshare", {"tool": TOOL, "email": NANCY}, key=key)
    assert res["isError"] is False and "no longer open" in text_of(res)
    res = call_tool(client, "set_access", {"tool": TOOL, "mode": "listed"}, key=key)
    assert "only the people it was shared with" in text_of(res)
    res = call_tool(client, "set_access", {"tool": TOOL, "mode": "members", "tier": "editor"}, key=key)
    assert "anyone in the workspace can open it as editor" in text_of(res)
    res = call_tool(client, "set_access", {"tool": TOOL, "mode": "public"}, key=key)
    assert res["isError"] is False and "public, anyone may read it" in text_of(res)
    res = call_tool(client, "set_access", {"tool": TOOL, "mode": "members"}, key=key)
    assert "anyone in the workspace can open it as" in text_of(res)


def test_secrets(client, key, tool):
    res = call_tool(client, "secrets_set", {"tool": TOOL, "name": "STRIPE_KEY", "value": "sk_test_x"}, key=key)
    assert res["isError"] is False and "set STRIPE_KEY" in text_of(res)
    res = call_tool(client, "secrets_list", {"tool": TOOL}, key=key)
    assert "STRIPE_KEY" in text_of(res) and "sk_test_x" not in text_of(res)
    res = call_tool(client, "secrets_set", {"tool": TOOL, "name": "lowercase", "value": "x"}, key=key)
    assert res["isError"] is True and "UPPER_SNAKE_CASE" in text_of(res)
    res = call_tool(client, "secrets_delete", {"tool": TOOL, "name": "STRIPE_KEY"}, key=key)
    assert res["isError"] is False and "deleted STRIPE_KEY" in text_of(res)


def test_users(client, key, owner):
    res = call_tool(client, "users_add", {"email": KIRA, "role": "member"}, key=key)
    assert res["isError"] is False and f"{KIRA} is a member" in text_of(res)
    assert "/join/" in text_of(res)
    res = call_tool(client, "users_list", key=key)
    assert res["isError"] is False
    text = text_of(res)
    assert owner in text and KIRA in text and "no password yet" in text
    assert {u["email"] for u in res["structuredContent"]["items"]} >= {owner, KIRA}
    res = call_tool(client, "users_invite", {"email": KIRA}, key=key)
    assert "secure confirmation email" in text_of(res) and "/join/inv_" in text_of(res)
    res = call_tool(client, "users_remove", {"email": KIRA}, key=key)
    assert f"removed {KIRA}" in text_of(res)
    assert KIRA not in text_of(call_tool(client, "users_list", key=key))


def test_domains_list_and_check(client, key, ws):
    res = call_tool(client, "domains_list", key=key)
    assert res["isError"] is False and f"{ws['slug']}.{PLATFORM}" in text_of(res)
    # the registrar is not connected in tests
    res = call_tool(client, "domain_check", {"domain": "example-tenant.com"}, key=key)
    assert res["isError"] is True and "registrar" in text_of(res)


def test_domain_buy_without_confirm_buys_nothing(client, key):
    before = call_tool(client, "domains_list", key=key)["structuredContent"]["domains"]
    res = call_tool(client, "domain_buy", {"domain": "example-tenant.com"}, key=key)
    # no registrar in tests, so even the quote fails; the message must say why
    assert res["isError"] is True
    assert "registrar" in text_of(res)
    after = call_tool(client, "domains_list", key=key)["structuredContent"]["domains"]
    assert before == after


def test_billing_and_prices(client, key, ws):
    res = call_tool(client, "billing", key=key)
    assert res["isError"] is False
    assert f"workspace {ws['slug']}: balance $" in text_of(res) and "card on file:" in text_of(res)
    assert "balance_cents" in res["structuredContent"]
    res = call_tool(client, "prices", key=key)
    assert res["isError"] is False and "per running tool per month" in text_of(res)


def test_topup_without_confirm_charges_nothing(client, key):
    before = call_tool(client, "billing", key=key)["structuredContent"]["balance_cents"]
    res = call_tool(client, "topup", {"cents": 2000}, key=key)
    assert res["isError"] is False
    assert "Dry run only" in text_of(res) and "No card on file" in text_of(res)
    assert res["structuredContent"]["dry_run"] is True
    after = call_tool(client, "billing", key=key)["structuredContent"]["balance_cents"]
    assert before == after
    res = call_tool(client, "topup", {"cents": 100}, key=key)
    assert res["isError"] is True and "$5.00" in text_of(res)


def test_card_link_without_stripe_is_a_clear_error(client, key):
    res = call_tool(client, "card_link", key=key)
    assert res["isError"] is True
    assert res["structuredContent"]["code"] == "CARD"


def test_delete_tool_purge_needs_a_second_confirmation(client, key, tool):
    res = call_tool(client, "delete_tool", {"tool": TOOL, "purge": True}, key=key)
    assert res["isError"] is True and "purge_confirm" in text_of(res)
    assert call_tool(client, "get_tool", {"tool": TOOL}, key=key)["isError"] is False


def test_missing_required_argument(client, key):
    res = call_tool(client, "get_tool", {}, key=key)
    assert res["isError"] is True and "tool is required" in text_of(res)
