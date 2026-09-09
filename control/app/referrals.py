"""Referrals: every person has a code. Sign up with someone's code and every tool is half price for sixty days;
the person whose code it was earns ten percent of what you pay for usage, tracked here per charge and paid in cash monthly."""
import secrets
import time

from . import config, db

DISCOUNT_DAYS = 60
DISCOUNT = 0.5           # of the tool rate, for the first sixty days
SHARE = 0.10             # of every usage charge, to the referrer, for as long as the workspace pays
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def code_for(email: str) -> str:
    """This person's code, minted the first time it is asked for. Looks like Casey-K3Q7."""
    email = email.lower().strip()
    with db.conn() as c:
        row = c.execute("SELECT code FROM referral_codes WHERE email=?", (email,)).fetchone()
        if row:
            return row["code"]
        stem = "".join(ch for ch in email.split("@")[0].upper() if ch.isalpha())[:6] or "BOAT"
        while True:
            code = stem + "-" + "".join(secrets.choice(ALPHABET) for _ in range(4))
            if not c.execute("SELECT 1 FROM referral_codes WHERE code=?", (code,)).fetchone():
                break
        c.execute("INSERT INTO referral_codes (code, email, created) VALUES (?,?,?)", (code, email, time.time()))
    return code


def lookup(code: str):
    """The email behind a code, or None."""
    with db.conn() as c:
        row = c.execute("SELECT email FROM referral_codes WHERE code=?", ((code or "").strip().upper(),)).fetchone()
    return row["email"] if row else None


def link_for(email: str) -> str:
    return f"https://{config.PLATFORM_DOMAIN}/signup?code={code_for(email)}"


def attach(workspace_id: str, referrer_email: str, when: float | None = None):
    when = when or time.time()
    with db.conn() as c:
        c.execute("UPDATE workspaces SET referred_by=?, discount_until=? WHERE id=?", (referrer_email, when + DISCOUNT_DAYS * 86400, workspace_id))


def tool_day_for(ws, base_cents: int, now: float | None = None) -> int:
    """The daily tool rate this workspace pays right now: half during the discount window."""
    until = ws["discount_until"] if "discount_until" in ws.keys() else None
    if until and (now or time.time()) < until:
        return int(round(base_cents * DISCOUNT))
    return base_cents


def earn(ws, charged_cents: int, ledger_ref: str):
    """Ten percent of a usage charge goes to the referrer. Idempotent on the charge's ledger ref."""
    referrer = ws["referred_by"] if "referred_by" in ws.keys() else None
    if not referrer or charged_cents <= 0:
        return 0
    cents = int(round(charged_cents * SHARE))
    if cents <= 0:
        return 0
    with db.conn() as c:
        if c.execute("SELECT 1 FROM referral_earnings WHERE ledger_ref=?", (ledger_ref,)).fetchone():
            return 0
        c.execute("INSERT INTO referral_earnings (id, referrer_email, workspace_id, cents, ledger_ref, created) VALUES (?,?,?,?,?,?)",
                  (db.new_id("re"), referrer, ws["id"], cents, ledger_ref, time.time()))
    return cents


def summary(email: str) -> dict:
    """What this person's code has done: who signed up with it, what they have paid, what is owed."""
    email = email.lower().strip()
    with db.conn() as c:
        referred = c.execute("SELECT id, slug, name, created, discount_until FROM workspaces WHERE referred_by=? ORDER BY created", (email,)).fetchall()
        rows = c.execute("SELECT workspace_id, SUM(cents) AS earned, SUM(CASE WHEN paid_at IS NULL THEN cents ELSE 0 END) AS unpaid "
                         "FROM referral_earnings WHERE referrer_email=? GROUP BY workspace_id", (email,)).fetchall()
    by_ws = {r["workspace_id"]: r for r in rows}
    out = []
    for w in referred:
        e = by_ws.get(w["id"])
        out.append({"workspace": w["slug"], "name": w["name"], "since": w["created"], "discount_until": w["discount_until"],
                    "earned_cents": int(e["earned"] if e else 0), "unpaid_cents": int(e["unpaid"] if e else 0)})
    return {"code": code_for(email), "link": link_for(email), "referred": out,
            "earned_cents": sum(x["earned_cents"] for x in out), "unpaid_cents": sum(x["unpaid_cents"] for x in out),
            "terms": f"{int(SHARE*100)}% of what they pay for usage, for as long as they pay, in cash every month; they get {int(DISCOUNT*100)}% off every tool for their first {DISCOUNT_DAYS} days"}


def payouts() -> list[dict]:
    """Unpaid earnings per referrer: the host owner's monthly list."""
    with db.conn() as c:
        rows = c.execute("SELECT referrer_email, SUM(cents) AS unpaid, COUNT(DISTINCT workspace_id) AS workspaces, MIN(created) AS oldest "
                         "FROM referral_earnings WHERE paid_at IS NULL GROUP BY referrer_email ORDER BY unpaid DESC").fetchall()
    return [{"email": r["referrer_email"], "unpaid_cents": int(r["unpaid"]), "workspaces": r["workspaces"], "oldest": r["oldest"]} for r in rows]


def mark_paid(email: str, payout_ref: str, by: str) -> int:
    with db.conn() as c:
        total = c.execute("SELECT COALESCE(SUM(cents),0) AS t FROM referral_earnings WHERE referrer_email=? AND paid_at IS NULL", (email.lower(),)).fetchone()["t"]
        c.execute("UPDATE referral_earnings SET paid_at=?, payout_ref=? WHERE referrer_email=? AND paid_at IS NULL", (time.time(), payout_ref, email.lower()))
    db.audit(by, "referral.paid", email, {"cents": int(total), "ref": payout_ref})
    return int(total)
