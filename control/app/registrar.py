"""The registrar behind `bh domain`. Boathouse holds ONE registrar account (the
host's); tenants buy domains through it and see only Boathouse. Porkbun today;
the surface is small enough that another registrar is a second class.

Keys are stored encrypted in the settings table. They arrive either through the
agent-first PKCE flow (connect_start / connect_finish: the account holder clicks
one link, nothing is copied) or by direct install (set_keys)."""
import base64
import hashlib
import secrets
import time

import httpx

from . import auth, db

BASE = "https://api.porkbun.com/api/json/v3"


class RegistrarError(Exception):
    def __init__(self, message: str, code: str | None = None, status: int = 422, uncertain: bool = False, replayed: bool = False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.uncertain = uncertain
        self.replayed = replayed


# ---- settings -----------------------------------------------------------------

def _get(name: str) -> str | None:
    with db.conn() as c:
        r = c.execute("SELECT value_enc FROM settings WHERE name=?", (name,)).fetchone()
    return auth.decrypt(r["value_enc"]) if r else None


def _set(name: str, value: str, by: str | None):
    with db.conn() as c:
        c.execute("""INSERT INTO settings VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                     value_enc=excluded.value_enc, updated=excluded.updated, updated_by=excluded.updated_by""",
                  (name, auth.encrypt(value), time.time(), by))


def _del(name: str):
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE name=?", (name,))


def creds() -> dict | None:
    k, s = _get("porkbun.apikey"), _get("porkbun.secret")
    return {"apikey": k, "secretapikey": s} if k and s else None


def is_sandbox() -> bool:
    k = _get("porkbun.apikey") or ""
    return k.startswith("pk1_sb_")


def set_keys(apikey: str, secret: str, by: str | None):
    _set("porkbun.apikey", apikey.strip(), by)
    _set("porkbun.secret", secret.strip(), by)
    _del("porkbun.pkce")


# ---- HTTP ---------------------------------------------------------------------

def _call(path: str, body: dict | None = None, authed: bool = True, idempotency_key: str | None = None, timeout: float = 30, method: str = "POST") -> dict:
    payload = dict(body or {})
    headers = {"Content-Type": "application/json", "User-Agent": "boathouse/0.2"}
    if authed:
        c = creds()
        if not c:
            raise RegistrarError("no registrar connected. Run: bh registrar connect", "NO_REGISTRAR", 409)
        headers["X-API-Key"] = c["apikey"]
        headers["X-Secret-API-Key"] = c["secretapikey"]
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as h:
                r = h.post(BASE + path, json=payload, headers=headers) if method == "POST" else h.get(BASE + path, headers=headers)
        except httpx.HTTPError as exc:
            raise RegistrarError("The registrar response was interrupted.", "PROVIDER_UNAVAILABLE", 503,
                                 uncertain=bool(idempotency_key)) from exc
        if r.status_code != 429:
            break
        # The registrar allows one check or purchase attempt per 10 seconds; wait it out rather than fail.
        try:
            wait = min(max(int(r.headers.get("Retry-After", "10") or 10), 0), 20) + 1
        except ValueError:
            wait = 11
        if attempt == 2:
            raise RegistrarError(f"registrar rate limit; retry in {wait}s", "RATE_LIMIT", 429, uncertain=bool(idempotency_key))
        time.sleep(wait)
    try:
        data = r.json()
    except ValueError:
        raise RegistrarError(f"registrar returned {r.status_code}: {r.text[:200]}", "BAD_RESPONSE", 502, uncertain=bool(idempotency_key))
    if not isinstance(data, dict):
        raise RegistrarError("The registrar returned an invalid response.", "BAD_RESPONSE", 502,
                             uncertain=bool(idempotency_key))
    if data.get("status") not in ("SUCCESS", "PENDING", "ERROR"):
        raise RegistrarError("The registrar returned an unrecognized result.", "BAD_RESPONSE", 502,
                             uncertain=bool(idempotency_key))
    if data.get("status") not in ("SUCCESS", "PENDING") or r.status_code >= 400:
        # An in-flight duplicate is not a failed purchase. A mismatched key or a
        # transient provider error also cannot prove the original operation failed.
        next_action = data.get("next_action") or {}
        pending = (r.status_code >= 500 or r.status_code in (408, 409)
                   or (r.status_code >= 400 and data.get("status") in ("SUCCESS", "PENDING"))
                   or data.get("code") in ("IDEMPOTENCY_KEY_IN_USE", "IDEMPOTENCY_KEY_MISMATCH")
                   or (isinstance(next_action, dict) and next_action.get("retryable") is True))
        raise RegistrarError(data.get("message") or f"registrar error {r.status_code}", data.get("code"),
                             503 if pending else 422, uncertain=bool(idempotency_key) and pending,
                             replayed=r.headers.get("Idempotent-Replayed", "").lower() == "true")
    return data


# ---- connect (PKCE) -------------------------------------------------------------

def connect_start(app_name: str, by: str | None) -> dict:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    r = _call("/apikey/request", {"name": app_name, "codeChallenge": challenge, "codeChallengeMethod": "S256"}, authed=False)
    _set("porkbun.pkce", f"{r['requestToken']}|{verifier}|{time.time()}", by)
    return {"auth_url": r["authUrl"], "expires": r.get("expiration"), "request_token": r["requestToken"]}


def connect_finish(by: str | None) -> dict:
    p = _get("porkbun.pkce")
    if not p:
        raise RegistrarError("no connect in progress. Run: bh registrar connect", "NO_PKCE", 409)
    token, verifier, started = p.split("|")
    r = _call("/apikey/retrieve", {"requestToken": token, "codeVerifier": verifier}, authed=False)
    if r.get("status") == "PENDING":
        return {"state": "pending", "started": float(started)}
    if not r.get("secretapikey"):
        raise RegistrarError(r.get("message") or "approval did not return a secret", r.get("code"))
    set_keys(r["apikey"], r["secretapikey"], by)
    return {"state": "connected", "sandbox": r["apikey"].startswith("pk1_sb_")}


def sandbox_keys(by: str | None) -> dict:
    """A throwaway sandbox pair: real catalog, fake money. For tests."""
    r = _call("/apikey/request", {"name": "Boathouse sandbox", "sandbox": True}, authed=False)
    set_keys(r["apikey"], r["secretapikey"], by)
    return {"state": "connected", "sandbox": True}


def status() -> dict:
    c = creds()
    if not c:
        pending = _get("porkbun.pkce")
        return {"connected": False, "pending": bool(pending)}
    try:
        ping = _call("/ping")
        bal = _call("/account/balance")
        return {"connected": True, "sandbox": is_sandbox(), "provider": "porkbun",
                "credentials_valid": ping.get("credentialsValid", True), "caller_ip": ping.get("yourIp"),
                "balance_cents": bal.get("balance"), "balance": bal.get("display")}
    except RegistrarError as e:
        return {"connected": True, "sandbox": is_sandbox(), "provider": "porkbun", "error": str(e), "code": e.code}


# ---- domains -------------------------------------------------------------------

def _cents(price) -> int:
    return int(round(float(str(price).replace(",", "")) * 100))


def check(domain: str) -> dict:
    r = _call(f"/domain/checkDomain/{domain}")
    res = r.get("response", {})
    renew = (res.get("additional") or {}).get("renewal") or {}
    return {"domain": domain, "available": res.get("avail") == "yes", "premium": res.get("premium") == "yes",
            "price_cents": _cents(res.get("price", 0)), "regular_cents": _cents(res.get("regularPrice") or res.get("price", 0)),
            "renewal_cents": _cents(renew.get("price")) if renew.get("price") else None,
            "first_year_promo": res.get("firstYearPromo") == "yes", "min_years": res.get("minDuration", 1)}


def buy(domain: str, cost_cents: int, dry_run: bool, idempotency_key: str | None = None) -> dict:
    if not dry_run and not idempotency_key:
        raise ValueError("A persisted purchase idempotency key is required.")
    body = {"cost": cost_cents, "agreeToTerms": "yes"}
    if dry_run:
        body["dryRun"] = True
    return _call(f"/domain/create/{domain}", body, idempotency_key=idempotency_key if not dry_run else None, timeout=90)


def list_owned() -> list[dict]:
    r = _call("/domain/listAll", {"includeLabels": "no"})
    return r.get("domains", [])


def owned(domain: str) -> bool:
    return any(d.get("domain", "").lower() == domain.lower() for d in list_owned())


# ---- DNS -----------------------------------------------------------------------

def records(domain: str) -> list[dict]:
    return _call(f"/dns/retrieve/{domain}").get("records", [])


def _delete(domain: str, rec_id: str):
    _call(f"/dns/delete/{domain}/{rec_id}")


def create_record(domain: str, name: str, rtype: str, content: str, ttl: int = 600, prio: int | None = None) -> str:
    body = {"type": rtype, "content": content, "ttl": str(ttl)}
    if name:
        body["name"] = name
    if prio is not None:
        body["prio"] = str(prio)
    return _call(f"/dns/create/{domain}", body).get("id", "")


def replace_record(domain: str, name: str, rtype: str, content: str, prio: int | None = None) -> dict:
    """Make name (relative; "" is the apex) carry exactly this one record of its type."""
    full = f"{name}.{domain}" if name else domain
    keep = None
    for rec in records(domain):
        if rec.get("name", "").lower() == full.lower() and rec.get("type", "").upper() == rtype.upper():
            same = rec.get("content", "").strip().rstrip(".") == content.strip().rstrip(".") and (prio is None or str(rec.get("prio") or "") in (str(prio), ""))
            if same and keep is None:
                keep = rec            # already right: leave it alone, so re-runs never blink the record
            else:
                _delete(domain, rec["id"])
    if keep:
        return {"name": full, "type": rtype, "content": content, "prio": prio, "id": keep["id"], "unchanged": True}
    rid = create_record(domain, name, rtype, content, prio=prio)
    return {"name": full, "type": rtype, "content": content, "prio": prio, "id": rid}


def point(domain: str, ip: str, names: tuple[str, ...] = ("", "*", "www")) -> list[dict]:
    """Make each name (relative to the zone; "" is the apex) an A record for ip,
    replacing whatever A/AAAA/ALIAS/CNAME the registrar parked there."""
    fqdn = {n: (f"{n}.{domain}" if n else domain) for n in names}
    done = []
    existing = records(domain)
    for n, full in fqdn.items():
        keep = None
        for rec in existing:
            if rec.get("name", "").lower() == full.lower() and rec.get("type") in ("A", "AAAA", "ALIAS", "CNAME"):
                if rec.get("type") == "A" and rec.get("content") == ip and keep is None:
                    keep = rec["id"]
                else:
                    _delete(domain, rec["id"])
        rid = keep or create_record(domain, n, "A", ip)
        done.append({"name": full, "type": "A", "content": ip, "id": rid})
    return done


def domain_info(domain: str) -> dict:
    result = _call(f"/domain/get/{domain}", method="GET")
    info = result.get("domain")
    if not isinstance(info, dict) or info.get("domain", "").lower() != domain:
        raise RegistrarError("The registrar did not return this domain's metadata.", "BAD_DOMAIN_PROOF")
    return info


def renewal_quote(domain: str) -> dict:
    result = _call(f"/domain/checkDomain/{domain}").get("response", {})
    # The primary price is ALWAYS registration, including first-year promos.
    # Despite the renew endpoint's prose suggesting priceType=renewal, the
    # documented response places renewal pricing in additional.renewal.
    renewal = (result.get("additional") or {}).get("renewal") or {}
    years = int(renewal.get("minDuration") or result.get("minDuration") or 1)
    cost = _cents(renewal.get("price", 0)) * years
    if result.get("premium") == "yes" or cost <= 0 or not 1 <= years <= 10:
        raise RegistrarError("The registrar cannot quote this renewal. Contact support.", "RENEWAL_UNSUPPORTED")
    return {"cost_cents": cost, "years": years}


def disable_auto_renew(domain: str):
    # This is a set-to-off operation, safe to repeat; never touch other domains.
    result = _call(f"/domain/updateAutoRenew/{domain}", {"status": "off"})
    per_domain = result.get("results", {}).get(domain, {})
    if per_domain.get("status") != "SUCCESS":
        raise RegistrarError("Could not turn off the registrar's separate automatic renewal.", "AUTORENEW_UNCONFIRMED")
    info = domain_info(domain)
    if str(info.get("autoRenew")) != "0":
        raise RegistrarError("The registrar still reports automatic renewal enabled.", "AUTORENEW_UNCONFIRMED")
    return info


def renew(domain: str, cost_cents: int, dry_run: bool, idempotency_key: str | None = None) -> dict:
    if not dry_run and not idempotency_key:
        raise ValueError("A persisted renewal idempotency key is required.")
    body = {"cost": cost_cents}
    if dry_run:
        body["dryRun"] = True
    return _call(f"/domain/renew/{domain}", body,
                 idempotency_key=idempotency_key if not dry_run else None, timeout=90)
