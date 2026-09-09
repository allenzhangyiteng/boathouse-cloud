"""Money and recovery invariants. No provider connection is permitted."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

os.environ.update(BH_METER="0", BH_DOMAIN="domain-review.test", BH_PUBLIC_IP="192.0.2.10",
                  BH_PG_PASSWORD="synthetic", BH_OWNERS="owner@review.test",
                  BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-domain-review-"))
sys.path.insert(0, str(Path(__file__).parents[1] / "control"))
import httpx
import pytest
from fastapi import HTTPException
from app import billing, config, db, domain_payments as payments, main, registrar


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "state.sqlite3")
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network prohibited"))
    db.init()
    ws = db.workspace()
    billing.post(ws["id"], "grant", 1000, "synthetic")
    monkeypatch.setattr(main, "_actor", lambda *a: {"workspace_id": ws["id"], "role": "owner", "email": "owner@review.test"})
    monkeypatch.setattr(registrar, "is_sandbox", lambda: True)
    monkeypatch.setattr(registrar, "creds", lambda: {"apikey": "synthetic", "secretapikey": "not-a-key"})
    monkeypatch.setattr(registrar, "check", lambda d: {"available": True, "premium": False, "price_cents": 400, "renewal_cents": 400})
    monkeypatch.setattr(main, "_point_dns", lambda d: {"records": []})
    calls = []
    def buy(domain, cost, dry_run, idempotency_key=None):
        if not dry_run:
            calls.append((domain, cost, idempotency_key))
        return {"status": "SUCCESS", "domain": domain, "cost": cost, "wouldSucceed": True, "orderId": len(calls) + 1000}
    monkeypatch.setattr(registrar, "buy", buy)
    return ws, calls


def quote(domain="domain-review.example"):
    return main.buy_domain(None, {"domain": domain, "confirm": False}, None)


def confirm(q, **kw):
    return main.buy_domain(None, {"domain": q["domain"], "confirm": True, "quote_id": q["quote_id"], "max_cost_cents": q["cost_cents"], **kw}, None)


def test_quote_does_not_spend_and_reuses_identity(isolate):
    ws, calls = isolate
    q = quote()
    assert q["cost_cents"] == 600
    assert quote()["quote_id"] == q["quote_id"]
    assert billing.balance(ws["id"]) == 1000 and calls == []


@pytest.mark.parametrize("body", [{"confirm": "false"}, {"confirm": 1}, {"confirm": True}, {"confirm": True, "quote_id": "missing", "max_cost_cents": True}])
def test_invalid_confirmation_never_calls_provider(body, isolate):
    with pytest.raises(HTTPException):
        main.buy_domain(None, {"domain": "domain-review.example", **body}, None)
    assert isolate[1] == []


def test_quote_binds_price_and_maximum(monkeypatch, isolate):
    q = quote()
    monkeypatch.setattr(registrar, "check", lambda d: pytest.fail("confirmation must not re-quote"))
    with pytest.raises(HTTPException) as exc:
        confirm(q, max_cost_cents=599)
    assert exc.value.detail["code"] == "PRICE_LIMIT"
    result = confirm(q)
    assert result["cost_cents"] == 600 and isolate[1][0][1] == 400


def test_expired_quote_never_spends(isolate):
    q = quote()
    with db.conn() as c:
        c.execute("UPDATE domain_operations SET expires=0 WHERE id=?", (q["quote_id"],))
    with pytest.raises(HTTPException) as exc:
        confirm(q)
    assert exc.value.detail["code"] == "QUOTE_EXPIRED" and isolate[1] == []


def test_quote_cannot_be_used_for_another_workspace(isolate, monkeypatch):
    q = quote()
    monkeypatch.setattr(main, "_actor", lambda *a: {"workspace_id": "other", "role": "owner", "email": "other@example.test"})
    monkeypatch.setattr(main, "_ws", lambda u: {"id": "other"})
    with pytest.raises(HTTPException) as exc:
        confirm(q)
    assert exc.value.status_code == 404 and isolate[1] == []


def test_concurrent_domains_cannot_overdraw(isolate, monkeypatch):
    ws, calls = isolate
    q1, q2 = quote("one-review.example"), quote("two-review.example")
    first_entered, allow_finish = threading.Event(), threading.Event()
    original = registrar.buy
    def slow_buy(*args, **kwargs):
        if not kwargs.get("dry_run"):
            first_entered.set()
            assert allow_finish.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(registrar, "buy", slow_buy)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm, q1)
        assert first_entered.wait(5)
        try:
            with pytest.raises(HTTPException) as exc:
                confirm(q2)
            assert exc.value.status_code == 402
            assert billing.balance(ws["id"]) == 400
        finally:
            allow_finish.set()
        assert first.result()["bought"] == q1["domain"]
    assert len(calls) == 1 and billing.balance(ws["id"]) == 400


def test_completed_retry_does_not_buy_or_debit_twice(isolate):
    q = quote()
    first, second = confirm(q), confirm(q)
    assert first["order_id"] == second["order_id"]
    assert len(isolate[1]) == 1
    assert billing.balance(isolate[0]["id"]) == 400
    assert len([r for r in billing.ledger(isolate[0]["id"]) if r["kind"] == "charge"]) == 1


def test_timeout_after_provider_success_retries_same_key(isolate, monkeypatch):
    q = quote()
    keys = []
    def interrupted(domain, cost, dry_run, idempotency_key=None):
        keys.append(idempotency_key)
        if len(keys) == 1:
            raise registrar.RegistrarError("response lost", "TIMEOUT", 503, uncertain=True)
        return {"status": "SUCCESS", "domain": domain, "cost": cost, "orderId": 1001}
    monkeypatch.setattr(registrar, "buy", interrupted)
    with pytest.raises(HTTPException) as exc:
        confirm(q)
    assert exc.value.detail["code"] == "PURCHASE_UNCERTAIN"
    assert billing.balance(isolate[0]["id"]) == 400
    assert quote()["quote_id"] == q["quote_id"]
    assert confirm(q)["order_id"] == 1001
    assert keys == [q["quote_id"], q["quote_id"]]


def test_known_rejection_returns_reservation_once(isolate, monkeypatch):
    q = quote()
    monkeypatch.setattr(registrar, "buy", lambda *a, **kw: (_ for _ in ()).throw(registrar.RegistrarError("price changed", "PRICE_CHANGED")))
    for _ in range(2):
        with pytest.raises(HTTPException):
            confirm(q)
    assert billing.balance(isolate[0]["id"]) == 1000
    rows = billing.ledger(isolate[0]["id"])
    assert len([r for r in rows if r["kind"] == "purchase_release"]) == 1


def test_lost_local_settlement_recovers_without_new_provider_call(isolate):
    q = quote()
    with db.conn() as c:
        c.execute("CREATE TRIGGER fail_domain_insert BEFORE INSERT ON domains BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END")
    with pytest.raises(HTTPException):
        confirm(q)
    with db.conn() as c:
        assert c.execute("SELECT count(*) FROM domains").fetchone()[0] == 0
        row = c.execute("SELECT * FROM domain_operations WHERE id=?", (q["quote_id"],)).fetchone()
        assert row["state"] == "uncertain" and row["provider_result"]
        assert c.execute("SELECT kind FROM ledger WHERE ref=?", ("domain:" + q["quote_id"],)).fetchone()[0] == "pending_purchase"
        c.execute("DROP TRIGGER fail_domain_insert")
    assert confirm(q)["bought"] == q["domain"]
    assert len(isolate[1]) == 1 and billing.balance(isolate[0]["id"]) == 400


def test_dns_failure_is_success_and_retry_repairs_without_charge(isolate, monkeypatch):
    q = quote()
    monkeypatch.setattr(main, "_point_dns", lambda d: (_ for _ in ()).throw(HTTPException(422, "synthetic DNS failure")))
    result = confirm(q)
    assert result["state"] == "purchased" and result["dns_pending"] and not result["dns_ok"]
    monkeypatch.setattr(main, "_point_dns", lambda d: {"records": [{"name": d, "content": "192.0.2.10"}]})
    result = confirm(q)
    assert result["dns_ok"] and not result["dns_pending"]
    assert len(isolate[1]) == 1 and billing.balance(isolate[0]["id"]) == 400


def test_stale_uncertain_does_not_replay_expired_provider_key(isolate, monkeypatch):
    q = quote()
    monkeypatch.setattr(registrar, "buy", lambda *a, **kw: (_ for _ in ()).throw(registrar.RegistrarError("timeout", uncertain=True)))
    with pytest.raises(HTTPException):
        confirm(q)
    with db.conn() as c:
        c.execute("UPDATE domain_operations SET started=?,lease_until=0 WHERE id=?", (time.time() - 24*3600, q["quote_id"]))
    monkeypatch.setattr(registrar, "buy", lambda *a, **kw: pytest.fail("cannot replay after provider expiry"))
    with pytest.raises(HTTPException) as exc:
        confirm(q)
    assert exc.value.detail["code"] == "RECONCILIATION_REQUIRED"
    assert billing.balance(isolate[0]["id"]) == 400


def test_dns_reads_require_workspace_ownership(isolate, monkeypatch):
    monkeypatch.setattr(registrar, "records", lambda d: pytest.fail("no other tenant DNS"))
    with pytest.raises(HTTPException) as exc:
        main.domain_records("other.example", None, None)
    assert exc.value.status_code == 404


def test_shared_registrar_domain_cannot_be_claimed_by_customer(isolate, monkeypatch):
    monkeypatch.setattr(main, "_is_host_owner", lambda u: False)
    monkeypatch.setattr(registrar, "owned", lambda d: True)
    with pytest.raises(HTTPException) as exc:
        main.attach_domain("host-owned.example", None, {}, None)
    assert exc.value.status_code == 403


def test_registrar_requires_persisted_write_key(monkeypatch):
    # Bypass the fixture's fake buy for this focused transport contract.
    import importlib.util
    spec = importlib.util.spec_from_file_location("app.registrar_contract", Path(registrar.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError):
        module.buy("domain-review.example", 400, False)
    sent = []
    monkeypatch.setattr(module, "_call", lambda *a, **kw: sent.append(kw) or {})
    module.buy("domain-review.example", 400, False, "persisted-quote")
    assert sent[0]["idempotency_key"] == "persisted-quote"


def test_concurrent_same_quote_only_one_provider_write(isolate, monkeypatch):
    q = quote()
    entered, finish = threading.Event(), threading.Event()
    original = registrar.buy
    def slow(*a, **kw):
        entered.set()
        assert finish.wait(5)
        return original(*a, **kw)
    monkeypatch.setattr(registrar, "buy", slow)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(confirm, q)
        assert entered.wait(5)
        try:
            with pytest.raises(HTTPException) as exc:
                confirm(q)
            assert exc.value.detail["code"] == "PURCHASE_PENDING"
        finally:
            finish.set()
        future.result()
    assert len(isolate[1]) == 1


def test_provider_account_change_refuses_spend(isolate, monkeypatch):
    q = quote()
    monkeypatch.setattr(registrar, "creds", lambda: {"apikey": "different-account"})
    with pytest.raises(HTTPException) as exc:
        confirm(q)
    assert exc.value.detail["code"] == "REGISTRAR_CHANGED" and isolate[1] == []


def test_completed_purchase_can_be_reattached_only_to_its_workspace(isolate, monkeypatch):
    q = quote()
    confirm(q)
    monkeypatch.setattr(registrar, "owned", lambda d: True)
    main.detach_domain(q["domain"], None, None)
    monkeypatch.setattr(main, "_is_host_owner", lambda u: False)
    assert main.attach_domain(q["domain"], None, {}, None)["attached"] == q["domain"]
    assert len(isolate[1]) == 1 and billing.balance(isolate[0]["id"]) == 400


def test_idempotent_dns_repair_preserves_correct_records(isolate, monkeypatch):
    records = [{"id": "apex", "name": "owned.example", "type": "A", "content": "192.0.2.10"},
               {"id": "wildcard", "name": "*.owned.example", "type": "A", "content": "192.0.2.10"},
               {"id": "www", "name": "www.owned.example", "type": "A", "content": "192.0.2.10"}]
    monkeypatch.setattr(registrar, "records", lambda d: records)
    monkeypatch.setattr(registrar, "_delete", lambda *a: pytest.fail("correct DNS must remain in place"))
    monkeypatch.setattr(registrar, "create_record", lambda *a: pytest.fail("correct DNS needs no rewrite"))
    assert len(registrar.point("owned.example", "192.0.2.10")) == 3


def test_mcp_confirmation_carries_prior_quote_without_requoting(isolate):
    import asyncio
    from app import mcp
    requests = []
    q = quote()
    class FakeApi:
        async def post(self, path, json):
            requests.append(json)
            return httpx.Response(200, json=main.buy_domain(None, json, None))
    args = {"domain": q["domain"], "confirm": True, "quote_id": q["quote_id"], "max_cost_cents": 600}
    _, result = asyncio.run(mcp.t_domain_buy(FakeApi(), args))
    assert result["bought"] == q["domain"] and requests == [args]
    with pytest.raises(mcp.ApiError):
        asyncio.run(mcp.t_domain_buy(FakeApi(), {"domain": q["domain"], "confirm": True}))


def test_cli_preserves_quote_identity_and_budget(isolate, monkeypatch):
    import importlib.machinery
    import importlib.util
    from types import SimpleNamespace
    path = str(Path(__file__).parents[1] / "cli" / "bh")
    loader = importlib.machinery.SourceFileLoader("domain_cli_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    sent = []
    def call(method, path, body=None, **kw):
        sent.append(body)
        return main.buy_domain(None, body, None)
    monkeypatch.setattr(cli, "call", call)
    cli.cmd_domain(SimpleNamespace(action="buy", name="domain-review.example", yes=True, to=None,
                                   quote_id=None, max_cost_cents=600))
    assert sent[1]["quote_id"] and sent[1]["max_cost_cents"] == 600 and sent[1]["confirm"] is True


def test_partial_local_ledger_settlement_cannot_strand_unpaid_domain(isolate):
    q = quote()
    with db.conn() as c:
        c.execute("CREATE TRIGGER fail_ledger_settle BEFORE UPDATE OF kind ON ledger WHEN NEW.kind='charge' BEGIN SELECT RAISE(ABORT,'synthetic settlement failure'); END")
    with pytest.raises(HTTPException):
        confirm(q)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM domains").fetchone()[0] == 0
        assert c.execute("SELECT state FROM domain_operations WHERE id=?", (q["quote_id"],)).fetchone()[0] == "uncertain"
        c.execute("DROP TRIGGER fail_ledger_settle")
    assert confirm(q)["state"] == "purchased" and len(isolate[1]) == 1


def test_attached_but_dns_pending_is_reported_truthfully(isolate, monkeypatch):
    monkeypatch.setattr(registrar, "owned", lambda d: True)
    monkeypatch.setattr(main, "_is_host_owner", lambda u: True)
    monkeypatch.setattr(main, "_point_dns", lambda d: (_ for _ in ()).throw(HTTPException(422, "DNS unavailable")))
    result = main.attach_domain("host-attach.example", None, {}, None)
    assert result["attached"] == "host-attach.example" and result["dns_pending"] and "note" in result


def test_legacy_domain_ownership_survives_detach_and_upgrade(isolate, monkeypatch):
    ws, _ = isolate
    with db.conn() as c:
        c.execute("INSERT INTO domains (id,workspace_id,domain,registrar,status,created) VALUES ('legacy',?,'legacy-review.example','porkbun','active',?)", (ws["id"], time.time()))
    db.init()  # Upgrade backfills ownership before the active attachment disappears.
    main.detach_domain("legacy-review.example", None, None)
    monkeypatch.setattr(main, "_is_host_owner", lambda u: False)
    monkeypatch.setattr(registrar, "owned", lambda d: True)
    assert main.attach_domain("legacy-review.example", None, {}, None)["attached"] == "legacy-review.example"
    with db.conn() as c:
        assert c.execute("SELECT workspace_id FROM domain_allocations WHERE domain='legacy-review.example'").fetchone()[0] == ws["id"]


def test_another_workspace_cannot_attach_detached_managed_domain(isolate, monkeypatch):
    q = quote()
    confirm(q)
    main.detach_domain(q["domain"], None, None)
    monkeypatch.setattr(main, "_ws", lambda u: {"id": "another-workspace"})
    monkeypatch.setattr(registrar, "owned", lambda d: True)
    with pytest.raises(HTTPException) as exc:
        main.attach_domain(q["domain"], None, {}, None)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("code", ["IDEMPOTENCY_KEY_IN_USE", "IDEMPOTENCY_KEY_MISMATCH"])
def test_registrar_idempotency_conflict_preserves_reservation(code, isolate, monkeypatch):
    q = quote()
    responses = [httpx.Response(409, json={"status": "ERROR", "code": code, "message": "existing operation"}),
                 httpx.Response(200, json={"status": "SUCCESS", "domain": q["domain"], "cost": 400, "orderId": 7001})]
    headers = []
    class Provider:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, json, headers):
            return response(dict(headers))
    def response(sent):
        headers.append(sent["Idempotency-Key"])
        return responses.pop(0)
    monkeypatch.setattr(httpx, "Client", lambda **kw: Provider())
    monkeypatch.setattr(registrar, "buy", lambda domain, cost, dry_run, idempotency_key=None: registrar._call(
        "/domain/create/" + domain, {"cost": cost, "agreeToTerms": "yes"}, idempotency_key=idempotency_key))
    with pytest.raises(HTTPException) as exc:
        confirm(q)
    assert exc.value.detail["code"] == "PURCHASE_UNCERTAIN"
    assert billing.balance(isolate[0]["id"]) == 400
    assert confirm(q)["order_id"] == 7001
    assert headers == [q["quote_id"], q["quote_id"]]
    assert not any(r["kind"] == "purchase_release" for r in billing.ledger(isolate[0]["id"]))


@pytest.mark.parametrize("replayed", [False, True])
def test_uncertain_retry_only_releases_a_replayed_original_rejection(replayed, isolate, monkeypatch):
    q = quote()
    failures = [registrar.RegistrarError("response lost", uncertain=True),
                registrar.RegistrarError("invalid key", "INVALID_API_KEYS_001", replayed=replayed)]
    def fail(*a, **kw): raise failures.pop(0)
    monkeypatch.setattr(registrar, "buy", fail)
    for _ in range(2):
        with pytest.raises(HTTPException): confirm(q)
    assert billing.balance(isolate[0]["id"]) == (1000 if replayed else 400)
    with db.conn() as c:
        row = c.execute("SELECT state FROM domain_operations WHERE id=?", (q["quote_id"],)).fetchone()
        assert row[0] == ("failed" if replayed else "uncertain")


@pytest.mark.parametrize("changes", [
    {"domain": None}, {"domain": "another.example"}, {"orderId": None}, {"orderId": True},
    {"orderId": "7001"}, {"cost": 401}, {"cost": "400"}, {"cost": True}, {"dryRun": True},
])
def test_incomplete_or_conflicting_purchase_proof_is_never_settled(changes, isolate, monkeypatch):
    q = quote()
    calls = []
    result = {"status": "SUCCESS", "domain": q["domain"], "cost": 400, "orderId": 7001, **changes}
    monkeypatch.setattr(registrar, "buy", lambda *a, **kw: calls.append(1) or result)
    for _ in range(2):
        with pytest.raises(HTTPException) as exc: confirm(q)
        assert exc.value.detail["code"] == "PURCHASE_UNCERTAIN"
    assert calls == [1]  # Stored evidence is rechecked without another registrar write.
    assert billing.balance(isolate[0]["id"]) == 400
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM domains").fetchone()[0] == 0
        assert c.execute("SELECT kind FROM ledger WHERE ref=?", ("domain:" + q["quote_id"],)).fetchone()[0] == "pending_purchase"


def test_success_cost_is_optional_under_official_response_schema(isolate, monkeypatch):
    q = quote()
    monkeypatch.setattr(registrar, "buy", lambda *a, **kw: {"status": "SUCCESS", "domain": q["domain"], "orderId": 7001})
    result = confirm(q)
    assert result["cost_cents"] == 600 and result["order_id"] == 7001


def test_domain_dns_replaces_www_parking_that_overrides_wildcard(isolate, monkeypatch):
    existing = [{"id": "apex", "name": "owned.example", "type": "A", "content": "192.0.2.10"},
                {"id": "wildcard", "name": "*.owned.example", "type": "A", "content": "192.0.2.10"},
                {"id": "parked-www", "name": "www.owned.example", "type": "CNAME", "content": "parking.example"}]
    deleted, created = [], []
    monkeypatch.setattr(registrar, "records", lambda d: existing)
    monkeypatch.setattr(registrar, "_delete", lambda d, rid: deleted.append(rid))
    monkeypatch.setattr(registrar, "create_record", lambda d, n, t, ip: created.append((n, t, ip)) or "new-www")
    result = registrar.point("owned.example", "192.0.2.10")
    assert deleted == ["parked-www"] and created == [("www", "A", "192.0.2.10")]
    assert {r["name"] for r in result} == {"owned.example", "*.owned.example", "www.owned.example"}


def namespace_workspace():
    with db.conn() as c:
        c.execute("INSERT INTO workspaces (id,slug,name,created) VALUES ('ws_namespace_other','namespace-other','Other workspace',?)", (time.time(),))
    return db.workspace_by_id('ws_namespace_other')


def attach_external(ws, domain):
    main._insert_domain(ws, domain, 'external', 'owner@example.test', None, None)


def test_nested_same_workspace_domain_resolves_most_specific_apex_www_auth(isolate):
    from app import hosts
    ws = isolate[0]
    attach_external(ws, 'namespace.example')  # Parent deliberately inserted first.
    attach_external(ws, 'child.namespace.example')
    with db.conn() as c:
        c.execute("UPDATE domains SET tool_slug='probe' WHERE domain='child.namespace.example'")
        c.execute("INSERT INTO tools (id,workspace_id,slug,name,signing_key,db_password,resource_key,created) VALUES ('t_namespace',?,'probe','Probe','key','password','r-namespace',?)", (ws['id'], time.time()))
    for host in ['child.namespace.example', 'www.child.namespace.example']:
        resolved = hosts.resolve(host)
        assert resolved.kind == 'tool' and resolved.slug == 'probe'
        assert resolved.base == 'child.namespace.example' and resolved.workspace['id'] == ws['id']
        assert main.tls_ask(host).status_code == 200
    resolved = hosts.resolve('auth.child.namespace.example')
    assert resolved.kind == 'auth' and resolved.base == 'child.namespace.example'
    assert main.tls_ask('auth.child.namespace.example').status_code == 200


@pytest.mark.parametrize('existing,proposed', [('namespace.example','child.namespace.example'), ('child.namespace.example','namespace.example')])
def test_cross_workspace_ancestor_or_child_attach_is_rejected(existing, proposed, isolate):
    attach_external(isolate[0], existing)
    other = namespace_workspace()
    with pytest.raises(HTTPException) as exc:
        attach_external(other, proposed)
    assert exc.value.detail['code'] == 'DOMAIN_NAMESPACE_CONFLICT'
    with db.conn() as c:
        assert c.execute('SELECT COUNT(*) FROM domains').fetchone()[0] == 1


def test_namespace_overlap_checks_label_boundaries(isolate):
    attach_external(isolate[0], 'namespace.example')
    attach_external(namespace_workspace(), 'othernamespace.example')
    with db.conn() as c:
        assert c.execute('SELECT COUNT(*) FROM domains').fetchone()[0] == 2


def test_concurrent_cross_workspace_nested_attach_has_one_winner(isolate):
    pairs = [(isolate[0], 'namespace.example'), (namespace_workspace(), 'child.namespace.example')]
    barrier = threading.Barrier(2)
    def attach(pair):
        barrier.wait(timeout=5)
        try:
            attach_external(*pair)
            return 200
        except HTTPException as exc:
            return exc.status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attach, pairs)) == [200, 409]


def test_domain_quote_rejects_other_workspace_descendant_before_provider(isolate, monkeypatch):
    attach_external(namespace_workspace(), 'child.namespace.example')
    monkeypatch.setattr(registrar, 'check', lambda d: pytest.fail('conflicting namespace must not reach provider'))
    with pytest.raises(HTTPException) as exc:
        quote('namespace.example')
    assert exc.value.detail['code'] == 'DOMAIN_NAMESPACE_CONFLICT'


def test_domain_confirm_rechecks_namespace_created_after_quote(isolate):
    q = quote('namespace.example')
    attach_external(namespace_workspace(), 'child.namespace.example')
    with pytest.raises(HTTPException) as exc:
        confirm(q)
    assert exc.value.detail['code'] == 'DOMAIN_NAMESPACE_CONFLICT'
    assert isolate[1] == [] and billing.balance(isolate[0]['id']) == 1000


def test_pending_domain_purchase_blocks_other_workspace_descendant(isolate):
    q = quote('namespace.example')
    row = payments._reserve(isolate[0], q['domain'], q['quote_id'], 600)
    with pytest.raises(HTTPException) as exc:
        attach_external(namespace_workspace(), 'child.namespace.example')
    assert exc.value.detail['code'] == 'DOMAIN_NAMESPACE_CONFLICT'
    payments._settle(row, {'status':'SUCCESS','domain':q['domain'],'cost':400,'orderId':12345})
    assert billing.balance(isolate[0]['id']) == 400


def test_retained_managed_domain_allocation_blocks_other_workspace_child(isolate):
    q = quote('namespace.example')
    confirm(q)
    main.detach_domain(q['domain'], None, None)
    with pytest.raises(HTTPException) as exc:
        attach_external(namespace_workspace(), 'child.namespace.example')
    assert exc.value.detail['code'] == 'DOMAIN_NAMESPACE_CONFLICT'


def test_legacy_cross_workspace_overlap_fails_closed_at_router_and_tls(isolate):
    from app import hosts
    attach_external(isolate[0], 'namespace.example')
    other = namespace_workspace()
    # Simulate an unsafe attachment written by an older release.
    with db.conn() as c:
        c.execute("INSERT INTO domains (id,workspace_id,domain,registrar,status,created) VALUES ('legacy_overlap',?,'child.namespace.example','external','active',?)", (other['id'], time.time()))
    assert hosts.resolve('child.namespace.example').kind == 'unknown'
    assert hosts.resolve('auth.child.namespace.example').kind == 'unknown'
    with pytest.raises(HTTPException) as exc:
        main.tls_ask('child.namespace.example')
    assert exc.value.status_code == 404
    assert hosts.resolve('namespace.example').workspace['id'] == isolate[0]['id']


def test_settlement_rechecks_namespace_and_preserves_registration_proof(isolate, monkeypatch):
    q = quote('namespace.example')
    other = namespace_workspace()
    def legacy_writer(domain, cost, dry_run, idempotency_key=None):
        # A legacy writer that did not participate in the new claim guard.
        with db.conn() as c:
            c.execute("INSERT INTO domains (id,workspace_id,domain,registrar,status,created) VALUES ('legacy_during_purchase',?,'child.namespace.example','external','active',?)", (other['id'], time.time()))
        return {'status':'SUCCESS','domain':domain,'cost':cost,'orderId':12345}
    monkeypatch.setattr(registrar, 'buy', legacy_writer)
    with pytest.raises(HTTPException) as exc:
        confirm(q)
    assert exc.value.detail['code'] == 'PURCHASE_UNCERTAIN'
    with db.conn() as c:
        row = c.execute('SELECT * FROM domain_operations WHERE id=?', (q['quote_id'],)).fetchone()
        assert row['state'] == 'uncertain' and json.loads(row['provider_result'])['orderId'] == 12345
        assert c.execute('SELECT 1 FROM domains WHERE domain=?', (q['domain'],)).fetchone() is None
        assert c.execute('SELECT kind FROM ledger WHERE ref=?', ('domain:' + q['quote_id'],)).fetchone()[0] == 'pending_purchase'
    assert billing.balance(isolate[0]['id']) == 400
