"""Durable domain quotes and purchases, paid from reserved workspace credit.

A quote is the purchase identity. A lost HTTP response can only retry that same
provider operation; it cannot turn a retry into a fresh purchase. Reservation,
settlement and release each commit together with their ledger/state metadata.
"""
import hashlib
import json
import time

from fastapi import HTTPException

from . import billing, db, registrar

QUOTE_SECONDS = 30 * 60
# Porkbun documents 24-hour replay. Leave a margin rather than retry at expiry.
REPLAY_SECONDS = 23 * 3600
LEASE_SECONDS = 360


def _error(status, code, message, **detail):
    raise HTTPException(status, {"code": code, "error": message, **detail})


def _fingerprint():
    keys = registrar.creds() or {}
    return hashlib.sha256(keys.get("apikey", "").encode()).hexdigest()


def _integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _error(422, "INVALID_AMOUNT", f"{label} must be an integer number of cents.")
    return value


def _operation(c, wid, operation_id, domain):
    row = c.execute("SELECT * FROM domain_operations WHERE id=? AND workspace_id=? AND domain=?",
                    (operation_id, wid, domain)).fetchone()
    if not row:
        _error(404, "QUOTE_NOT_FOUND", "That purchase quote does not belong to this workspace and domain.")
    return row


def require_namespace(c, workspace_id, domain):
    """A custom domain and every descendant must belong to one workspace.

    Call while holding the write transaction when claiming a name. Pending
    purchases and retained ownership count even before/after a live attachment.
    """
    conflict = c.execute("""SELECT 1 FROM (
        SELECT workspace_id,domain FROM domains WHERE status='active'
        UNION SELECT workspace_id,domain FROM domain_allocations
        UNION SELECT workspace_id,domain FROM domain_operations
              WHERE state IN ('processing','uncertain','purchased')
        ) WHERE workspace_id<>? AND
          (domain=? OR domain LIKE ? OR ? LIKE '%.' || domain) LIMIT 1""",
        (workspace_id, domain, '%.' + domain, domain)).fetchone()
    if conflict:
        _error(409, "DOMAIN_NAMESPACE_CONFLICT",
               "This domain overlaps another workspace's address. Use a separate domain.")


def quote_view(row):
    bal = billing.balance(row["workspace_id"])
    reserved = row["state"] in ("processing", "uncertain", "purchased")
    return {"dry_run": True, "quote_id": row["id"], "operation_id": row["id"],
            "domain": row["domain"], "cost_cents": row["total_cents"], "cost": f"${row['total_cents']/100:.2f}",
            "registrar_cents": row["registrar_cents"], "margin_cents": row["margin_cents"],
            "renewal_cents": row["renewal_cents"], "balance_cents": bal,
            "renewal_policy": "Buying enables automatic renewal from your Boat House credit, up to the quoted renewal price per renewal. We email you first and wait at least seven days. Turn it off in your account at any time before renewal starts; higher prices require your approval.",
            "sufficient_funds": reserved or bal >= row["total_cents"],
            "would_succeed": reserved or bal >= row["total_cents"],
            "message": None if reserved or bal >= row["total_cents"] else "Add credit before buying this domain.",
            "sandbox": bool(row["sandbox"]), "expires": row["expires"], "state": row["state"]}


def quote(ws, domain, by):
    with db.conn() as c:
        require_namespace(c, ws["id"], domain)
        existing = c.execute("""SELECT * FROM domain_operations WHERE workspace_id=? AND domain=?
            AND (state IN ('processing','uncertain','purchased') OR (state='quoted' AND expires>?))
            ORDER BY created DESC LIMIT 1""", (ws["id"], domain, time.time())).fetchone()
        attached = c.execute("SELECT workspace_id FROM domains WHERE domain=?", (domain,)).fetchone()
    if existing:
        return quote_view(existing)
    if attached:
        _error(409, "ATTACHED", f"{domain} is already attached to a workspace.")
    q = registrar.check(domain)
    if not q["available"]:
        _error(409, "TAKEN", f"{domain} is not available.")
    if q["premium"]:
        _error(409, "PREMIUM", f"{domain} is a premium domain and cannot be bought here.")
    # Registrations cover the registry's minimum duration, which can exceed one year.
    cost = _integer(q["price_cents"], "Registrar price", 1) * int(q.get("min_years") or 1)
    dry = registrar.buy(domain, cost, dry_run=True)
    reg_cost = _integer(dry.get("cost", cost), "Registrar price", 1)
    if dry.get("wouldSucceed") is False or dry.get("sufficientFunds") is False:
        _error(409, "WOULD_FAIL", dry.get("message") or "The registrar cannot complete this purchase yet.")
    margin = billing.PRICES["domain_margin"] * int(q.get("min_years") or 1)
    now = time.time()
    oid = db.new_id("dq")
    with db.conn() as c:
        c.execute("""INSERT INTO domain_operations
            (id,workspace_id,domain,state,registrar_cents,margin_cents,total_cents,renewal_cents,
             sandbox,provider_fingerprint,created,expires,created_by)
            VALUES (?,?,?,'quoted',?,?,?,?,?,?,?,?,?)""",
            (oid, ws["id"], domain, reg_cost, margin, reg_cost + margin,
             (q.get("renewal_cents") or q.get("regular_cents") or reg_cost) + billing.PRICES["domain_margin"],
             int(registrar.is_sandbox()), _fingerprint(), now, now + QUOTE_SECONDS, by))
        row = _operation(c, ws["id"], oid, domain)
    return quote_view(row)


def _reserve(ws, domain, oid, maximum):
    _integer(maximum, "max_cost_cents", 1)
    now = time.time()
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = _operation(c, ws["id"], oid, domain)
        require_namespace(c, ws["id"], domain)
        if row["total_cents"] > maximum:
            _error(409, "PRICE_LIMIT", "The quote exceeds the approved spending limit.",
                   quote_id=oid, cost_cents=row["total_cents"], max_cost_cents=maximum)
        if row["state"] == "purchased":
            return dict(row)
        if row["provider_fingerprint"] != _fingerprint() or bool(row["sandbox"]) != registrar.is_sandbox():
            _error(409, "REGISTRAR_CHANGED", "The registrar account changed; this operation needs review.", quote_id=oid)
        if row["state"] == "failed":
            _error(409, "PURCHASE_FAILED", "This purchase was rejected and its reserved credit was returned. Request a new quote.", quote_id=oid)
        if row["state"] in ("processing", "uncertain"):
            if not row["provider_result"] and now - row["started"] >= REPLAY_SECONDS:
                _error(409, "RECONCILIATION_REQUIRED", "The registrar result still needs review. Reserved credit is protected; another purchase will not be attempted.", quote_id=oid)
            if row["lease_until"] and row["lease_until"] > now:
                _error(409, "PURCHASE_PENDING", "This purchase is still being checked. Retry the same quote shortly.", quote_id=oid)
        else:
            if row["expires"] <= now:
                _error(409, "QUOTE_EXPIRED", "This quote expired. Request a fresh quote before buying.", quote_id=oid)
            if c.execute("SELECT 1 FROM domains WHERE domain=?", (domain,)).fetchone():
                _error(409, "ATTACHED", f"{domain} is already attached to a workspace.")
            if c.execute("SELECT 1 FROM domain_allocations WHERE domain=?", (domain,)).fetchone():
                _error(409, "ALLOCATED", "This domain is already owned through Boathouse; attach it instead of buying it again.")
            if c.execute("SELECT 1 FROM domain_operations WHERE domain=? AND state IN ('processing','uncertain','purchased')", (domain,)).fetchone():
                _error(409, "PURCHASE_PENDING", "This domain already has a purchase in progress.")
            balance = c.execute("SELECT balance_cents FROM workspaces WHERE id=?", (ws["id"],)).fetchone()[0]
            if balance < row["total_cents"]:
                _error(402, "INSUFFICIENT_FUNDS", "Add credit before buying this domain.", quote_id=oid,
                       balance_cents=balance, cost_cents=row["total_cents"])
            after = balance - row["total_cents"]
            c.execute("UPDATE workspaces SET balance_cents=? WHERE id=?", (after, ws["id"]))
            c.execute("""INSERT INTO ledger (id,workspace_id,ts,kind,amount_cents,balance_after,memo,ref,created_by)
                VALUES (?,?,?,'pending_purchase',?,?,?,?,?)""",
                (db.new_id("l"), ws["id"], now, -row["total_cents"], after,
                 f"Reserved for domain {domain}; awaiting registrar confirmation", f"domain:{oid}", row["created_by"]))
        c.execute("UPDATE domain_operations SET state='processing',started=COALESCE(started,?),lease_until=? WHERE id=?",
                  (now, now + LEASE_SECONDS, oid))
        updated = dict(_operation(c, ws["id"], oid, domain))
        updated["retrying_uncertain"] = row["state"] in ("processing", "uncertain")
        c.execute("COMMIT")
        return updated


def _uncertain(oid, message):
    with db.conn() as c:
        c.execute("UPDATE domain_operations SET state='uncertain',lease_until=0,error=? WHERE id=? AND state='processing'",
                  (message[:400], oid))


def _release(row, message):
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        current = _operation(c, row["workspace_id"], row["id"], row["domain"])
        if current["state"] in ("failed", "purchased"):
            return
        bal = c.execute("SELECT balance_cents FROM workspaces WHERE id=?", (row["workspace_id"],)).fetchone()[0]
        after = bal + row["total_cents"]
        c.execute("UPDATE workspaces SET balance_cents=? WHERE id=?", (after, row["workspace_id"]))
        c.execute("""INSERT INTO ledger (id,workspace_id,ts,kind,amount_cents,balance_after,memo,ref,created_by)
            VALUES (?,?,?,'purchase_release',?,?,?,?,?)""", (db.new_id("l"), row["workspace_id"], time.time(),
            row["total_cents"], after, f"Domain {row['domain']} was not purchased; reserved credit returned",
            f"domain-release:{row['id']}", row["created_by"]))
        c.execute("UPDATE domain_operations SET state='failed',lease_until=0,error=? WHERE id=?", (message[:400], row["id"]))
        c.execute("COMMIT")


def _validate_purchase_result(row, result):
    """Require the registrar's documented registration proof before settlement.

    Its OpenAPI schema requires status/domain/orderId. Cost is optional there;
    when present it must match the exact cost in our immutable purchase request.
    """
    valid = (isinstance(result, dict) and result.get("status") == "SUCCESS"
             and isinstance(result.get("domain"), str)
             and result["domain"].lower().rstrip(".") == row["domain"]
             and type(result.get("orderId")) is int
             and result.get("dryRun", False) is False)
    if isinstance(result, dict) and "cost" in result:
        valid = valid and type(result["cost"]) is int and result["cost"] == row["registrar_cents"]
    if not valid:
        raise registrar.RegistrarError("The registrar returned incomplete or conflicting purchase proof.",
                                       "PURCHASE_PROOF_INVALID", 503, uncertain=True)


def _settle(row, result):
    # Persist the provider response before settlement too: after a local storage
    # failure, a retry can settle from this proof without another provider request.
    with db.conn() as c:
        c.execute("UPDATE domain_operations SET provider_result=? WHERE id=?", (json.dumps(result), row["id"]))
    _validate_purchase_result(row, result)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        current = _operation(c, row["workspace_id"], row["id"], row["domain"])
        if current["state"] == "purchased":
            return
        try:
            require_namespace(c, row["workspace_id"], row["domain"])
        except HTTPException as exc:
            # The registration already succeeded; retain its proof and credit
            # reservation until the conflicting namespace is reconciled.
            raise registrar.RegistrarError("The domain's workspace allocation needs review before activation.",
                                           "DOMAIN_NAMESPACE_CONFLICT", 503, uncertain=True) from exc
        first = c.execute("SELECT COUNT(*) FROM domains WHERE workspace_id=? AND status='active'", (row["workspace_id"],)).fetchone()[0] == 0
        c.execute("""INSERT INTO domains (id,workspace_id,domain,registrar,status,is_primary,dns_ok,cost_cents,order_id,created,created_by)
            VALUES (?,?,?,'porkbun','active',?,0,?,?,?,?)""", (db.new_id("d"), row["workspace_id"], row["domain"],
            int(first), row["registrar_cents"], str(result.get("orderId") or ""), time.time(), row["created_by"]))
        c.execute("INSERT INTO domain_allocations (domain,workspace_id,created) VALUES (?,?,?)",
                  (row["domain"], row["workspace_id"], time.time()))
        c.execute("""INSERT OR IGNORE INTO domain_renewal_settings
            (domain,workspace_id,enabled,max_cost_cents,provider_fingerprint,sandbox,approved_by,updated)
            VALUES (?,?,1,?,?,?,?,?)""", (row["domain"],row["workspace_id"],row["renewal_cents"],
            row["provider_fingerprint"],row["sandbox"],row["created_by"],time.time()))
        c.execute("UPDATE ledger SET kind='charge',memo=? WHERE ref=?", (
            f"domain {row['domain']} (registrar ${row['registrar_cents']/100:.2f} + ${row['margin_cents']/100:.2f})", f"domain:{row['id']}"))
        c.execute("UPDATE domain_operations SET state='purchased',lease_until=0,settled=?,error=NULL WHERE id=?", (time.time(), row["id"]))
        c.execute("INSERT INTO audit (ts,workspace_id,actor,action,target,detail) VALUES (?,?,?,'domain.buy',?,?)", (
            time.time(), row["workspace_id"], row["created_by"], row["domain"],
            json.dumps({"cost_cents": row["total_cents"], "order": result.get("orderId"), "sandbox": bool(row["sandbox"]), "quote_id": row["id"]})))
        c.execute("COMMIT")


def purchase(ws, domain, oid, maximum):
    if not isinstance(oid, str) or not oid:
        _error(422, "QUOTE_REQUIRED", "Request a quote, then confirm it with quote_id and max_cost_cents.")
    row = _reserve(ws, domain, oid, maximum)
    if row["state"] == "purchased":
        return row
    try:
        if row["provider_result"]:
            result = json.loads(row["provider_result"])
        else:
            result = registrar.buy(domain, row["registrar_cents"], dry_run=False, idempotency_key=oid)
        if result.get("status") == "PENDING":
            _uncertain(oid, "The registrar is still processing this purchase.")
            _error(409, "PURCHASE_PENDING", "The registrar is still processing this purchase. Retry the same quote shortly.", quote_id=oid)
        _settle(row, result)
    except registrar.RegistrarError as exc:
        if exc.uncertain or (row.get("retrying_uncertain") and not exc.replayed):
            # A fresh auth/validation rejection on a retry says nothing about a
            # response lost earlier. Only a replayed original rejection releases it.
            _uncertain(oid, str(exc))
            _error(503, "PURCHASE_UNCERTAIN", "The registrar response was interrupted. Credit remains reserved; retry this same quote to check the result.", quote_id=oid)
        _release(row, str(exc))
        _error(exc.status, exc.code or "PURCHASE_FAILED", str(exc), quote_id=oid)
    except HTTPException:
        raise
    except Exception:
        _uncertain(oid, "The purchase result needs reconciliation.")
        _error(503, "PURCHASE_UNCERTAIN", "The purchase result needs checking. Credit remains reserved; retry this same quote.", quote_id=oid)
    with db.conn() as c:
        return dict(_operation(c, ws["id"], oid, domain))
