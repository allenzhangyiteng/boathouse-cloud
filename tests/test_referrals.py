"""No welcome credit; a referral code halves the tool rate for sixty days and pays the referrer ten percent of every charge."""
import os
import sys
import tempfile
import time

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PUBLIC_IP="203.0.113.4", BH_PG_PASSWORD="x",
                  BH_OWNERS="owner@starter.test", BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-ref-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, db, deploy, main, referrals  # noqa: E402

PLAT = "platform.test"
API = {"host": f"api.{PLAT}"}
P = {"host": PLAT}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver", follow_redirects=False) as cl:
        yield cl


def test_no_welcome_credit_and_a_code_at_signup(client):
    maria = main.create_workspace("Maria Ref", "maria-ref@fixture.test")
    assert billing.balance(db.workspace("maria-ref")["id"]) == 0 and maria["welcome_credit_cents"] == 0
    code = referrals.code_for("maria-ref@fixture.test")
    assert code.startswith("MARIAR-") and referrals.code_for("maria-ref@fixture.test") == code           # stable, six letters of the name
    with pytest.raises(Exception) as e:
        main.create_workspace("Self Ref", "maria-ref@fixture.test", code=code)
    assert "your own code" in str(e.value.detail)
    with pytest.raises(Exception) as e:
        main.create_workspace("Bad Ref", "someone-ref@fixture.test", code="NOPE-0000")
    assert "not one of ours" in str(e.value.detail)
    sam = main.create_workspace("Sam Ref", "sam-ref@fixture.test", code=code.lower())                    # case does not matter
    ws = db.workspace("sam-ref")
    assert sam["referred_by"] == "maria-ref@fixture.test" and ws["referred_by"] == "maria-ref@fixture.test"
    assert ws["discount_until"] - ws["created"] == pytest.approx(60 * 86400)
    assert referrals.tool_day_for(ws, billing.TOOL_DAY) == 16 and billing.TOOL_DAY == 33


def test_a_deploy_on_an_empty_balance_is_refused(client):
    ws = db.workspace("sam-ref")
    key = auth.create_project_key(ws["id"], "sam-ref@fixture.test", "k")
    r = client.post("/api/tools/hello/deploys", headers={**API, "Authorization": f"Bearer {key}"}, files={"context": ("ctx.tar", b"x")})
    assert r.status_code == 402 and "put money on the balance before deploying" in r.json()["detail"] and "$0.16 a day" in r.json()["detail"]


def test_metering_halves_the_rate_and_pays_the_referrer(client, monkeypatch):
    ws = db.workspace("sam-ref")
    billing.post(ws["id"], "topup", 2000, "test money", "t:sam-ref", "test")
    with db.conn() as c:
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_ref", ws["id"], "hello", "Hello", "k", "pw", time.time(), "sam-ref@fixture.test"))
    monkeypatch.setattr(deploy, "status", lambda slug: {"state": "running"})
    monkeypatch.setattr(billing, "_storage_gb", lambda t: 0.0)
    monkeypatch.setattr(billing, "pause", lambda ws, why: None)          # other test workspaces sit at zero; no docker here
    monkeypatch.setattr(billing, "resume", lambda ws: None)
    lines = billing.meter_once("2026-09-10")
    mine = [l for l in lines if l["workspace"] == "sam-ref"]
    assert mine == [{"workspace": "sam-ref", "tool": "hello", "cents": 16, "gb": 0.0}]
    assert billing.balance(ws["id"]) == 2000 - 16
    s = referrals.summary("maria-ref@fixture.test")
    assert s["earned_cents"] == 2 and s["unpaid_cents"] == 2 and s["referred"][0]["workspace"] == "sam-ref"
    billing.meter_once("2026-09-10")                                                                # same day twice: nothing doubles
    assert billing.balance(ws["id"]) == 2000 - 16 and referrals.summary("maria-ref@fixture.test")["earned_cents"] == 2
    with db.conn() as c:                                                                            # after the sixty days: full rate, referrer still earns
        c.execute("UPDATE workspaces SET discount_until=? WHERE id=?", (time.time() - 1, ws["id"]))
    billing.meter_once("2026-11-20")
    assert billing.balance(ws["id"]) == 2000 - 16 - 33 and referrals.summary("maria-ref@fixture.test")["earned_cents"] == 2 + 3


def test_referral_api_and_payouts(client):
    ws = db.workspace("maria-ref")
    mk = auth.create_project_key(ws["id"], "maria-ref@fixture.test", "k")
    r = client.get("/api/referral", headers={**API, "Authorization": f"Bearer {mk}"}).json()
    assert r["code"] == referrals.code_for("maria-ref@fixture.test") and r["link"].endswith(f"/signup?code={r['code']}") and r["unpaid_cents"] == 5
    from app import config
    if not db.workspace(config.HOST_WORKSPACE):
        main.create_workspace(config.HOST_WORKSPACE.title(), "owner@starter.test")
    hk = auth.create_project_key(db.workspace(config.HOST_WORKSPACE)["id"], "owner@starter.test", "ops")
    h = {**API, "Authorization": f"Bearer {hk}"}
    assert client.get("/api/referral/payouts", headers={**API, "Authorization": f"Bearer {mk}"}).status_code == 403
    owed = client.get("/api/referral/payouts", headers=h).json()["payouts"]
    assert [o["email"] for o in owed] == ["maria-ref@fixture.test"] and owed[0]["unpaid_cents"] == 5
    r = client.post("/api/referral/payouts/maria-ref@fixture.test", headers=h, json={"ref": "PayPal 2026-10-01"})
    assert r.status_code == 200 and r.json()["paid_cents"] == 5
    assert client.get("/api/referral/payouts", headers=h).json()["payouts"] == []
    assert client.get("/api/referral", headers={**API, "Authorization": f"Bearer {mk}"}).json()["unpaid_cents"] == 0


def test_signup_page_takes_a_code_and_says_so(client):
    code = referrals.code_for("maria-ref@fixture.test")
    page = client.get(f"/signup?code={code}", headers=P).text
    assert f'value="{code}"' in page and "Half price on every tool" in page and "of credit" not in page


def test_a_tool_deleted_and_deployed_again_is_one_tool_for_the_day(client, monkeypatch):
    ws = db.workspace("sam-ref")
    billing.post(ws["id"], "topup", 1000, "test money", "t:sam-ref-2", "test")
    monkeypatch.setattr(deploy, "status", lambda slug: {"state": "running"})
    monkeypatch.setattr(billing, "_storage_gb", lambda t: 0.0)
    monkeypatch.setattr(billing, "pause", lambda ws, why: None)
    monkeypatch.setattr(billing, "resume", lambda ws: None)
    with db.conn() as c:
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_again1", ws["id"], "again", "Again", "k", "pw", time.time(), "sam-ref@fixture.test"))
    billing.meter_once("2026-12-01")
    after_one = billing.balance(ws["id"])
    with db.conn() as c:
        c.execute("DELETE FROM tools WHERE id='t_again1'")
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_again2", ws["id"], "again", "Again", "k", "pw", time.time(), "sam-ref@fixture.test"))
    billing.meter_once("2026-12-01")
    assert billing.balance(ws["id"]) == after_one
    # a charge written before the by-name ref existed (old id-keyed ref, gone tool id) still counts for the day
    billing.post(ws["id"], "charge", -33, "again: running day 2026-12-02", "meter:t_gone:2026-12-02", "meter")
    billing.meter_once("2026-12-02")
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) FROM ledger WHERE workspace_id=? AND memo LIKE 'again: running day 2026-12-02%'", (ws["id"],)).fetchone()[0]
    assert n == 1
    with db.conn() as c:
        c.execute("DELETE FROM tools WHERE id='t_again2'")
