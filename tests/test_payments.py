"""Durable, bounded payments with a synthetic idempotent Stripe transport.

Uses fresh SQLite state, real API/helper code and blocks network connections.
No provider credentials, cards, mail or real charges are used.
"""
import asyncio
import concurrent.futures
import hashlib
import hmac
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

os.environ.setdefault("BH_STATE_DIR", tempfile.mkdtemp(prefix="bh-payments-"))
os.environ.setdefault("BH_DOMAIN", "provider-review.test")
os.environ.update(BH_METER="0", BH_PG_PASSWORD="synthetic")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "control"))

import httpx
import pytest
from fastapi import HTTPException
from app import billing, config, db, front, main, mcp, pages


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "state.sqlite3")
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network prohibited"))
    db.init()
    ws = db.workspace()
    with db.conn() as c:
        c.execute("UPDATE workspaces SET stripe_customer='cus_synthetic',stripe_pm='pm_synthetic',autorefill_cents=500,autorefill_cap_cents=500 WHERE id=?", (ws["id"],))
    monkeypatch.setattr(main, "_actor", lambda *a: {"workspace_id": ws["id"], "role": "owner", "email": "owner@review.test"})
    monkeypatch.setattr(billing, "_setting", lambda name: "synthetic")
    return db.workspace()


class Processor:
    def __init__(self, monkeypatch):
        self.intents = {}
        self.requests = []
        self.lose_response = False
        self.status = "succeeded"
        monkeypatch.setattr(httpx, "Client", lambda **kw: self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def request(self, method, url, data=None, headers=None):
        self.requests.append((method, url, data, headers))
        if method == "GET":
            pi = next(p for p in self.intents.values() if p["id"] == url.rsplit("/", 1)[-1])
        else:
            key = headers["Idempotency-Key"]
            pi = self.intents.setdefault(key, {"id": "pi_" + hashlib.sha256(key.encode()).hexdigest()[:20], "status": self.status,
                "amount": data["amount"], "amount_received": data["amount"], "currency": data["currency"],
                "customer": data["customer"], "metadata": {"boathouse_operation": data["metadata[boathouse_operation]"]}})
        if self.lose_response:
            self.lose_response = False
            raise httpx.ReadTimeout("response lost after provider commit")
        return httpx.Response(200, json=pi)


def quote(ws, cents=500):
    return billing.quote_topup(ws, cents, "owner@review.test")["operation_id"]


def pay(ws, opid, cents=500):
    return billing.charge_card(ws, cents, "top-up", "owner@review.test", opid)


def test_timeout_retry_across_minute_and_card_change_reuses_exact_request(setup, monkeypatch):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    opid = quote(setup)
    other_quote = quote(setup, 1000)
    with pytest.raises(billing.StripeError, match="not confirmed"):
        pay(setup, opid)
    assert billing.balance(setup["id"]) == 0
    with pytest.raises(billing.StripeError, match="previous"):
        quote(setup, 1000)
    with pytest.raises(billing.StripeError, match="previous"):
        pay(setup, other_quote, 1000)
    assert quote(setup) == opid
    now = time.time()
    monkeypatch.setattr(billing.time, "time", lambda: now + 65)
    with db.conn() as c:
        c.execute("UPDATE workspaces SET stripe_pm='pm_changed' WHERE id=?", (setup["id"],))
    assert pay(setup, opid) == 500
    assert len(processor.intents) == 1
    assert processor.requests[0][2:] == processor.requests[1][2:]


def test_concurrent_confirm_and_ledger_are_exactly_once(setup, monkeypatch):
    processor = Processor(monkeypatch)
    opid = quote(setup)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: pay(setup, opid), range(4))) == [500] * 4
    assert len(processor.requests) == 1
    assert len(billing.ledger(setup["id"])) == 1


def test_distinct_approved_payments_have_distinct_provider_keys(setup, monkeypatch):
    processor = Processor(monkeypatch)
    first = quote(setup)
    pay(setup, first)
    second = quote(setup)
    assert first != second
    pay(setup, second)
    assert len(processor.intents) == 2 and billing.balance(setup["id"]) == 1000


def test_unknown_older_than_retention_never_resubmits(setup, monkeypatch):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    opid = quote(setup)
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    with db.conn() as c:
        c.execute("UPDATE payment_operations SET submitted=? WHERE id=?", (time.time() - 25*3600, opid))
    with pytest.raises(billing.StripeError, match="Contact support"):
        pay(setup, opid)
    with pytest.raises(billing.StripeError):
        quote(setup, 1000)
    assert len(processor.requests) == 1


def test_retry_in_separate_python_process_uses_persisted_provider_identity(setup, monkeypatch, tmp_path):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    opid = quote(setup)
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    fixture = tmp_path / "synthetic-provider-result.json"
    fixture.write_text(json.dumps({"key": next(iter(processor.intents)), "pi": next(iter(processor.intents.values())),
                                  "payload": processor.requests[0][2]}))
    script = r'''
import json, os, pathlib, socket, sys, time
sys.path.insert(0, sys.argv[1])
from app import billing, config, db
config.DB_PATH = pathlib.Path(sys.argv[2])
fixture = json.loads(pathlib.Path(sys.argv[3]).read_text())
socket.socket.connect = lambda *a: (_ for _ in ()).throw(AssertionError("network prohibited"))
billing._setting = lambda name: "synthetic"
now = time.time()
billing.time.time = lambda: now + 65
class Client:
    def __init__(self, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def request(self, method, url, data=None, headers=None):
        assert method == "POST"
        assert headers["Idempotency-Key"] == fixture["key"]
        assert data == fixture["payload"]
        return billing.httpx.Response(200, json=fixture["pi"])
billing.httpx.Client = Client
ws = db.workspace_by_id(sys.argv[4])
balance = billing.charge_card(ws, 500, "retry", "owner", sys.argv[5])
print(json.dumps({"balance": balance, "ledger_lines": len(billing.ledger(ws["id"]))}))
'''
    env = dict(os.environ, BH_STATE_DIR=str(tmp_path), BH_DOMAIN=config.PLATFORM_DOMAIN, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", script, str(Path(__file__).resolve().parents[1] / "control"),
                             str(config.DB_PATH), str(fixture), setup["id"], opid], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"balance": 500, "ledger_lines": 1}
    assert pay(setup, opid) == 500 and len(processor.requests) == 1


def webhook(pi):
    payload = json.dumps({"type": "payment_intent.succeeded", "data": {"object": pi}}).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(b"synthetic", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return billing.handle_webhook(payload, f"t={stamp},v1=retired-signature,v1={signature}")


def test_webhook_recovers_lost_response_and_replay_is_safe(setup, monkeypatch):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    opid = quote(setup)
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    pi = next(iter(processor.intents.values()))
    assert webhook(pi)["credited"] is True
    assert webhook(pi)["credited"] is True
    assert pay(setup, opid) == 500
    assert len(processor.requests) == 1 and len(billing.ledger(setup["id"])) == 1


def test_webhook_rejects_wrong_amount_or_customer(setup, monkeypatch):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    opid = quote(setup)
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    pi = next(iter(processor.intents.values()))
    for change in ({"amount": 600}, {"customer": "cus_other"}, {"currency": "eur"}):
        with pytest.raises(billing.StripeError, match="did not match"):
            webhook(dict(pi, **change))
    assert billing.balance(setup["id"]) == 0


def test_failed_charge_cannot_credit_and_fresh_quote_can_retry(setup, monkeypatch):
    processor = Processor(monkeypatch)
    processor.status = "requires_payment_method"
    opid = quote(setup)
    with pytest.raises(billing.StripeError, match="declined"):
        pay(setup, opid)
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    assert len(processor.requests) == 1 and billing.balance(setup["id"]) == 0
    assert quote(setup) != opid


def test_quote_scope_amount_expiry_and_strict_confirm(setup, monkeypatch):
    processor = Processor(monkeypatch)
    opid = quote(setup)
    for body in ({"cents": 500, "confirm": "false"}, {"cents": "500"}, {"cents": 500.0}, {"cents": 500, "confirm": 1}, {"cents": 500, "confirm": True}):
        with pytest.raises(HTTPException):
            main.billing_topup(None, body, None)
    with pytest.raises(billing.StripeError):
        pay(setup, opid, 1000)
    other = dict(setup, id="ws_unrelated")
    with pytest.raises(billing.StripeError):
        billing.quote_topup(other, 500, "owner", opid)
    with db.conn() as c:
        c.execute("UPDATE payment_operations SET expires=0 WHERE id=?", (opid,))
    with pytest.raises(billing.StripeError, match="expired"):
        pay(setup, opid)
    assert processor.requests == []


def test_auto_refill_serializes_and_enforces_fresh_balance_and_cap(setup, monkeypatch):
    processor = Processor(monkeypatch)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: billing.auto_refill(setup), range(4))) == [500] * 4
    assert len(processor.requests) == 1
    billing.post(setup["id"], "charge", -500, "synthetic usage")
    with pytest.raises(billing.StripeError, match="limit reached"):
        billing.auto_refill(setup)
    assert len(processor.requests) == 1


def test_auto_refill_ignores_stale_enabled_settings(setup, monkeypatch):
    processor = Processor(monkeypatch)
    billing.set_auto_refill(setup, 0, 0)
    assert billing.auto_refill(setup) == 0
    assert processor.requests == []


def test_auto_refill_pending_blocks_manual_and_survives_changed_cap(setup, monkeypatch):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    with pytest.raises(billing.StripeError):
        billing.auto_refill(setup)
    with pytest.raises(billing.StripeError, match="previous"):
        quote(setup)
    billing.set_auto_refill(setup, 500, 50)
    assert billing.auto_refill(setup) == 500  # already-approved operation recovers; no second charge
    assert len(processor.intents) == 1


def test_disable_pending_refill_does_not_send_new_post(setup, monkeypatch):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    with pytest.raises(billing.StripeError):
        billing.auto_refill(setup)
    billing.set_auto_refill(setup, 0, 0)
    with pytest.raises(billing.StripeError, match="now off"):
        billing.auto_refill(setup)
    assert len(processor.requests) == 1
    webhook(next(iter(processor.intents.values())))
    assert billing.balance(setup["id"]) == 500


def test_auto_refill_rejects_invalid_caps_and_small_remainder(setup, monkeypatch):
    processor = Processor(monkeypatch)
    for cap in (0, -1, 49, "500", 500.0):
        with pytest.raises(billing.StripeError):
            billing.set_auto_refill(setup, 500, cap)
    billing.set_auto_refill(setup, 500, 501)
    billing.post(setup["id"], "topup", 500, "auto-refill legacy")
    billing.post(setup["id"], "charge", -500, "synthetic usage")
    with pytest.raises(billing.StripeError, match="minimum"):
        billing.auto_refill(setup)
    assert processor.requests == []


def test_polling_reuses_form_quotes_and_exposes_recovery(setup, monkeypatch):
    processor = Processor(monkeypatch)
    results = [billing.form_quotes(setup, "owner") for _ in range(20)]
    assert all(r == results[0] for r in results)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM payment_operations").fetchone()[0] == 2
    processor.lose_response = True
    with pytest.raises(billing.StripeError):
        pay(setup, results[0]["2000"], 2000)
    view = dict(setup, csrf="test", platform="example.test", card_on_file=True,
                topup_quotes=billing.form_quotes(setup, "owner"), pending_payment=billing.pending_payment(setup))
    html = pages._money_block(view, "account")
    assert "Check the previous payment" in html and results[0]["2000"] in html


def test_account_form_double_submit_is_one_charge(setup, monkeypatch):
    from test_checkout import CheckoutProcessor
    processor = CheckoutProcessor(monkeypatch)
    opid = quote(setup, 2000)
    monkeypatch.setattr(front, "_guard", lambda *a: ("owner@review.test", [], db.workspace(), None))
    for _ in range(2):
        result = front.account_topup(setup["slug"], None, "20", "csrf", "welcome", opid)
        assert result.status_code == 303
    assert len(processor.sessions) == 1 and billing.balance(setup["id"]) == 0


def test_cli_persists_quote_through_lost_response_and_new_process_globals(setup, monkeypatch, tmp_path):
    from test_checkout import CheckoutProcessor
    processor = CheckoutProcessor(monkeypatch)
    processor.lose_response = True
    cli_path = Path(__file__).resolve().parents[1] / "cli" / "bh"
    def command():
        namespace = runpy.run_path(str(cli_path), run_name="bh_payment_test")
        globals_ = namespace["cmd_billing"].__globals__
        globals_["CONFIG"] = tmp_path / "cli" / "config.json"
        globals_["load_cfg"] = lambda: {"api": "https://api.example.test", "workspace": setup["slug"]}
        globals_["call"] = lambda method, path, body=None, **kw: main.billing_topup(None, body, None)
        return namespace["cmd_billing"]
    args = SimpleNamespace(action="topup", arg1="5", yes=True)
    with pytest.raises(HTTPException):
        command()(args)
    assert len(list((tmp_path / "cli").glob("payment-*.json"))) == 1
    command()(args)
    processor.pay()
    command()(args)
    assert len(processor.sessions) == 1 and billing.balance(setup["id"]) == 500
    assert list((tmp_path / "cli").glob("payment-*.json")) == []


def test_mcp_quote_and_explicit_confirmation_use_same_operation(setup, monkeypatch):
    from test_checkout import CheckoutProcessor
    processor = CheckoutProcessor(monkeypatch)
    class Api:
        async def post(self, path, json):
            result = main.billing_topup(None, json, None)
            return SimpleNamespace(json=lambda: result)
    api = Api()
    _, q = asyncio.run(mcp.t_topup(api, {"cents": 500}))
    assert processor.requests == []
    with pytest.raises(Exception):
        asyncio.run(mcp.t_topup(api, {"cents": 500, "confirm": "false"}))
    _, confirmed = asyncio.run(mcp.t_topup(api, {"cents": 500, "confirm": True, "operation_id": q["operation_id"]}))
    assert confirmed["operation_id"] == q["operation_id"] and len(processor.sessions) == 1 and confirmed["requires_checkout"]


def test_cent_parser_is_exact_and_rejects_nan():
    assert billing.dollars_to_cents("19.99") == 1999
    for value in ("1.001", "NaN", "Infinity", "anything"):
        with pytest.raises(billing.StripeError):
            billing.dollars_to_cents(value)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_retry_api_error_cannot_clear_original_uncertain_charge(setup, monkeypatch, status):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    opid = quote(setup)
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    monkeypatch.setattr(processor, "request", lambda *a, **kw: httpx.Response(status, json={"error": {"message": "Synthetic credential or lookup failure"}}))
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    with db.conn() as c:
        assert c.execute("SELECT status FROM payment_operations WHERE id=?", (opid,)).fetchone()[0] == "unknown"
    assert quote(setup) == opid
    with pytest.raises(billing.StripeError):
        quote(setup, 1000)
    assert billing.balance(setup["id"]) == 0


@pytest.mark.parametrize("wrong", [{"customer": "cus_elsewhere"}, {"amount": 501}, {"metadata": {}}, {"metadata": "invalid"}])
def test_unrelated_decline_cannot_release_payment_hold(setup, monkeypatch, wrong):
    processor = Processor(monkeypatch)
    processor.lose_response = True
    opid = quote(setup)
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    pi = dict(next(iter(processor.intents.values())), status="requires_payment_method", **wrong)
    monkeypatch.setattr(processor, "request", lambda *a, **kw: httpx.Response(402, json={"error": {"message": "declined", "payment_intent": pi}}))
    with pytest.raises(billing.StripeError, match="did not match"):
        pay(setup, opid)
    assert quote(setup) == opid
    with db.conn() as c:
        assert c.execute("SELECT status FROM payment_operations WHERE id=?", (opid,)).fetchone()[0] == "unknown"


def test_bound_decline_in_error_response_is_definitive(setup, monkeypatch):
    processor = Processor(monkeypatch)
    processor.status = "requires_payment_method"
    original = processor.request
    def declined(*a, **kw):
        pi = original(*a, **kw).json()
        return httpx.Response(402, json={"error": {"message": "declined", "payment_intent": pi}})
    monkeypatch.setattr(processor, "request", declined)
    opid = quote(setup)
    with pytest.raises(billing.StripeError, match="declined"):
        pay(setup, opid)
    with db.conn() as c:
        assert c.execute("SELECT status FROM payment_operations WHERE id=?", (opid,)).fetchone()[0] == "failed"
    assert quote(setup) != opid and billing.balance(setup["id"]) == 0


def test_first_submission_auth_failure_is_safe_to_retry_fresh(setup, monkeypatch):
    processor = Processor(monkeypatch)
    monkeypatch.setattr(processor, "request", lambda *a, **kw: httpx.Response(401, json={"error": {"message": "invalid key"}}))
    opid = quote(setup)
    with pytest.raises(billing.StripeError):
        pay(setup, opid)
    assert quote(setup) != opid and billing.balance(setup["id"]) == 0
