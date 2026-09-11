"""Boathouse's own sign-in: invite links, passwords, sessions, project keys, and
the signing of identity headers. No third party is involved, so no console
anywhere has to be visited to set it up.

A person is added by email (bh users add / bh share). That mints an invite link.
Opening it once sets a password and starts a year-long session. The session
cookie is scoped to whichever domain the person came in on (a workspace's own
domain, or its free <workspace>.<platform> address), so every tool on that
domain shares it. An agent authenticates with a project key bound to the person
who created it, so audit rows always name a human."""
import base64
import hashlib
import hmac
import json
import math
import secrets
import time

from cryptography.fernet import Fernet

from . import config, db


def master_key() -> bytes:
    p = config.MASTER_KEY_PATH
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(Fernet.generate_key())
        p.chmod(0o600)
    return p.read_bytes().strip()


def fernet() -> Fernet:
    return Fernet(master_key())


def encrypt(value: str) -> str:
    return fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return fernet().decrypt(token.encode()).decode()


# ---- signed short-lived tokens (CSRF, form state) ----------------------------

def sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(master_key(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def verify(token: str | None) -> dict | None:
    try:
        raw, sig = (token or "").split(".", 1)
        if not hmac.compare_digest(hmac.new(master_key(), raw.encode(), hashlib.sha256).hexdigest()[:32], sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if not isinstance(payload, dict):
            return None
        expires = float(payload.get("exp", 0))
        if not math.isfinite(expires) or expires < time.time():
            return None
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return None
    return payload


def csrf_token(scope: str) -> str:
    return sign({"csrf": scope, "exp": time.time() + 3600})


def csrf_ok(token: str | None, scope: str) -> bool:
    p = verify(token)
    return bool(p and p.get("csrf") == scope)


# ---- passwords ---------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 15, r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, h = stored.split("$")
    got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 15, r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)
    return hmac.compare_digest(got, base64.b64decode(h))


def password_problem(pw: str) -> str | None:
    if len(pw) < config.MIN_PASSWORD:
        return f"use at least {config.MIN_PASSWORD} characters"
    if pw.lower() in ("password" * 2, "passwordpassword", "123456789012"):
        return "pick something less guessable"
    return None


# A small in-memory brake on password guessing. Per (ip, email); resets on restart.
_attempts: dict[str, list[float]] = {}


def throttle(key: str, limit: int = 8, window: int = 900) -> bool:
    """True when the caller must wait."""
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < window]
    _attempts[key] = hits
    return len(hits) >= limit


def record_attempt(key: str):
    _attempts.setdefault(key, []).append(time.time())


# ---- accounts: one password per email, across every workspace ---------------

def account(email: str):
    with db.conn() as c:
        return c.execute("SELECT * FROM accounts WHERE email=?", (email.lower(),)).fetchone()


def has_password(email: str) -> bool:
    with db.conn() as c:
        return _has_password(c, email.lower())


def _has_password(c, email: str) -> bool:
    # Include pre-account rows: an invitation must never overwrite a legacy login.
    return bool(c.execute("SELECT 1 FROM accounts WHERE email=? AND pw_hash IS NOT NULL", (email,)).fetchone()
                or c.execute("SELECT 1 FROM users WHERE email=? AND pw_hash IS NOT NULL", (email,)).fetchone())


def _write_password(c, email: str, pw_hash: str, name: str | None):
    c.execute("""INSERT INTO accounts (email, pw_hash, name, created, last_login) VALUES (?,?,?,?,NULL)
                 ON CONFLICT(email) DO UPDATE SET pw_hash=excluded.pw_hash, name=COALESCE(excluded.name, accounts.name)""",
              (email, pw_hash, name, time.time()))
    c.execute("UPDATE users SET pw_hash=?, name=COALESCE(?, name) WHERE email=?", (pw_hash, name, email))


def set_account_password(email: str, password: str, name: str | None = None):
    email = email.lower()
    pw_hash = hash_password(password)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _write_password(c, email, pw_hash, name)
        c.execute("COMMIT")


def check_login(email: str, password: str) -> bool:
    """Password check against the account. A row that predates accounts is
    checked against its workspace copy and promoted on success."""
    email = email.lower()
    a = account(email)
    if a and a["pw_hash"]:
        return check_password(password, a["pw_hash"])
    with db.conn() as c:
        rows = c.execute("SELECT pw_hash, name FROM users WHERE email=? AND pw_hash IS NOT NULL", (email,)).fetchall()
    for r in rows:
        if check_password(password, r["pw_hash"]):
            set_account_password(email, password, r["name"])
            return True
    return False


def touch_login(email: str):
    with db.conn() as c:
        c.execute("UPDATE accounts SET last_login=? WHERE email=?", (time.time(), email.lower()))


PLATFORM = "platform"           # sessions.workspace_id for a Boathouse-level (not tenant) session


def create_platform_session(email: str) -> str:
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with db.conn() as c:
        c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (sid, PLATFORM, email.lower(), None, now, now + config.SESSION_DAYS * 86400))
    touch_login(email)
    return sid


def platform_email(sid: str | None) -> str | None:
    if not sid:
        return None
    with db.conn() as c:
        s = c.execute("SELECT email FROM sessions WHERE id=? AND workspace_id=? AND expires>?", (sid, PLATFORM, time.time())).fetchone()
    return s["email"] if s else None


def memberships(email: str) -> list:
    """Every workspace this email belongs to, owners first."""
    with db.conn() as c:
        return c.execute("""SELECT w.*, u.role AS my_role FROM users u JOIN workspaces w ON w.id=u.workspace_id
                            WHERE u.email=? ORDER BY (u.role='owner') DESC, w.created""", (email.lower(),)).fetchall()


# ---- invites -----------------------------------------------------------------

def spend_token(token: str, expires: float | None) -> bool:
    """Mark a signed one-time token as used. False if it was already spent. Spent hashes age out with the token."""
    now = time.time()
    with db.conn() as c:
        c.execute("DELETE FROM used_tokens WHERE expires < ?", (now - 60,))
        used = c.execute("INSERT OR IGNORE INTO used_tokens (hash, expires) VALUES (?,?)",
                         (_token_hash(token), float(expires or now + 900)))
        return used.rowcount == 1


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(workspace_id: str, email: str, created_by: str | None) -> str:
    """One live invite per email: a new one replaces the old."""
    token = "inv_" + secrets.token_urlsafe(32)
    now = time.time()
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM invites WHERE workspace_id=? AND email=? AND used IS NULL", (workspace_id, email.lower()))
        c.execute("INSERT INTO invites VALUES (?,?,?,?,?,?,?,NULL)",
                  (db.new_id("i"), workspace_id, email.lower(), _token_hash(token), now,
                   now + config.INVITE_DAYS * 86400, created_by))
        c.execute("COMMIT")
    return token


def invite_for(token: str, include_inactive: bool = False):
    with db.conn() as c:
        return c.execute("SELECT * FROM invites WHERE token_hash=? AND (? OR (used IS NULL AND expires>?))",
                         (_token_hash(token), include_inactive, time.time())).fetchone()


def invite_proof(token: str) -> str | None:
    """Secret mailbox proof, added to email only, never to an owner's response."""
    inv = invite_for(token)
    if not inv:
        return None
    return sign({"invite": inv["id"], "token": inv["token_hash"], "email": inv["email"], "exp": inv["expires"]})


def invite_proof_ok(inv, proof: str | None) -> bool:
    p = verify(proof)
    return bool(inv and p and p.get("invite") == inv["id"] and p.get("token") == inv["token_hash"] and p.get("email") == inv["email"])


def accept_invite(inv, password: str, name: str | None, proof: str | None = None) -> bool:
    """Initialize a new account, once. A workspace invitation is never password recovery.

    The account and token checks share a write transaction, including across
    invitations issued by different workspace owners for the same email.
    """
    if not inv or not invite_proof_ok(inv, proof):
        return False
    pw_hash = hash_password(password)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        live = c.execute("SELECT * FROM invites WHERE id=? AND used IS NULL AND expires>?", (inv["id"], time.time())).fetchone()
        if not live or _has_password(c, live["email"]) or not c.execute(
                "SELECT 1 FROM users WHERE workspace_id=? AND email=?", (live["workspace_id"], live["email"])).fetchone():
            return False
        _write_password(c, live["email"], pw_hash, name or None)
        c.execute("UPDATE invites SET used=? WHERE email=? AND used IS NULL", (time.time(), live["email"]))
        c.execute("COMMIT")
    return True


def needs_invite(user) -> bool:
    return user is not None and not has_password(user["email"])


def signup_token(workspace: str, email: str, code: str, purpose: str = "") -> str:
    return encrypt(json.dumps({"signup": True, "workspace": workspace, "email": email, "code": code, "purpose": purpose, "exp": time.time() + 3600}))


def signup_details(token: str) -> dict | None:
    try:
        p = json.loads(decrypt(token))
        if not isinstance(p, dict) or p.get("signup") is not True or not math.isfinite(float(p.get("exp", 0))) or p["exp"] < time.time():
            return None
        if not all(isinstance(p.get(key), str) for key in ("workspace", "email", "code")):
            return None
    except Exception:
        return None
    with db.conn() as c:
        if c.execute("SELECT 1 FROM used_tokens WHERE hash=?", (_token_hash(token),)).fetchone():
            return None
    return p


# Recovery proves mailbox ownership. Its tokens are never returned to a workspace owner.
RESET_TTL = 30 * 60


def create_password_reset(email: str) -> str | None:
    a = account(email)
    if not a or not a["pw_hash"]:
        return None
    return sign({"reset": a["email"], "pw": _token_hash(a["pw_hash"]),
                 "exp": time.time() + RESET_TTL, "n": secrets.token_urlsafe(16)})


def credential_stamp(email: str) -> str | None:
    a = account(email)
    return _token_hash(a["pw_hash"]) if a and a["pw_hash"] else None


def password_reset_for(token: str) -> dict | None:
    p = verify(token)
    if not p or not isinstance(p.get("reset"), str) or not p.get("n"):
        return None
    a = account(p["reset"])
    if not a or not a["pw_hash"] or p.get("pw") != _token_hash(a["pw_hash"]):
        return None
    with db.conn() as c:
        if c.execute("SELECT 1 FROM used_tokens WHERE hash=?", (_token_hash(token),)).fetchone():
            return None
    return p


def reset_password(token: str, password: str) -> str | None:
    p = verify(token)
    if not p or not isinstance(p.get("reset"), str) or not p.get("n"):
        return None
    pw_hash = hash_password(password)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        a = c.execute("SELECT * FROM accounts WHERE email=?", (p["reset"],)).fetchone()
        if not a or not a["pw_hash"] or p.get("pw") != _token_hash(a["pw_hash"]):
            return None
        used = c.execute("INSERT OR IGNORE INTO used_tokens (hash, expires) VALUES (?,?)", (_token_hash(token), p["exp"]))
        if used.rowcount != 1:
            return None
        _write_password(c, a["email"], pw_hash, None)
        # Recovery must also evict existing browser and agent access.
        for table in ("sessions", "project_keys", "claims"):
            c.execute(f"DELETE FROM {table} WHERE email=?", (a["email"],))
        c.execute("UPDATE invites SET used=? WHERE email=? AND used IS NULL", (time.time(), a["email"]))
        c.execute("COMMIT")
    return a["email"]


# ---- sessions -----------------------------------------------------------------

def create_session(workspace_id: str, email: str, name: str | None) -> str:
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with db.conn() as c:
        c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                  (sid, workspace_id, email.lower(), name, now, now + config.SESSION_DAYS * 86400))
        c.execute("UPDATE users SET last_login=? WHERE workspace_id=? AND email=?", (now, workspace_id, email.lower()))
    return sid


def session_user(sid: str | None, workspace_id: str | None = None):
    """The users row for a live session, or None. A session for an email that
    has since been removed from the workspace is dead, not just stale. When a
    workspace is given the session must belong to it."""
    if not sid:
        return None
    with db.conn() as c:
        s = c.execute("SELECT * FROM sessions WHERE id=? AND expires>?", (sid, time.time())).fetchone()
        if not s or (workspace_id and s["workspace_id"] != workspace_id):
            return None
        return c.execute("SELECT * FROM users WHERE workspace_id=? AND email=?",
                         (s["workspace_id"], s["email"])).fetchone()


def delete_session(sid: str | None):
    if sid:
        with db.conn() as c:
            c.execute("DELETE FROM sessions WHERE id=?", (sid,))


def revoke_sessions(workspace_id: str, email: str):
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE workspace_id=? AND email=?", (workspace_id, email.lower()))


def revoke_all_sessions(email: str):
    """After a password change: every tenant session and the platform session for this email."""
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE email=?", (email.lower(),))


# ---- project keys (for the CLI and agents) ------------------------------------

def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


CLAIM_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O/1/I/L
CLAIM_TTL = 15 * 60


def create_claim(workspace_id: str, email: str, key: str) -> str:
    """A one-time code that stands in for a project key on the line people paste into an agent.
    The code is what lands in chats and shell histories; it stops working after one use or fifteen minutes."""
    code = "BH-" + "-".join("".join(secrets.choice(CLAIM_ALPHABET) for _ in range(4)) for _ in range(3))
    with db.conn() as c:
        c.execute("INSERT INTO claims (code, workspace_id, email, key_enc, created, expires, used) VALUES (?,?,?,?,?,?,NULL)",
                  (code, workspace_id, email, encrypt(key), time.time(), time.time() + CLAIM_TTL))
    return code


def redeem_claim(code: str) -> dict | None:
    """The key behind a code, once. None when the code is unknown, used, or expired."""
    if not isinstance(code, str):
        return None
    code = code.strip().upper()
    with db.conn() as c:
        row = c.execute("""UPDATE claims SET used=? WHERE code=? AND used IS NULL AND expires>?
                           AND EXISTS (SELECT 1 FROM workspaces WHERE id=claims.workspace_id)
                           RETURNING *""", (time.time(), code, time.time())).fetchone()
    if not row:
        return None
    key = decrypt(row["key_enc"])
    with db.conn() as c:
        valid = c.execute("""SELECT 1 FROM project_keys k JOIN users u ON u.email=k.email
                             WHERE k.key_hash=? AND k.email=? AND u.workspace_id=? AND k.workspace_id IN (?,?)""",
                          (_key_hash(key), row["email"], row["workspace_id"], row["workspace_id"], ACCOUNT)).fetchone()
    if not valid:
        return None
    return {"workspace_id": row["workspace_id"], "email": row["email"], "key": key}


def create_project_key(workspace_id: str, email: str, name: str) -> str:
    key = "bh_" + secrets.token_urlsafe(30)
    with db.conn() as c:
        c.execute("INSERT INTO project_keys VALUES (?,?,?,?,?,?,?)",
                  (db.new_id("pk"), workspace_id, email.lower(), name, _key_hash(key), time.time(), None))
    return key


ACCOUNT = "*"   # a key whose workspace is "*" is the person: it works in every workspace they belong to


def key_row(bearer: str | None):
    if not bearer or not bearer.startswith("bh_"):
        return None
    with db.conn() as c:
        k = c.execute("SELECT * FROM project_keys WHERE key_hash=?", (_key_hash(bearer),)).fetchone()
        if k:
            c.execute("UPDATE project_keys SET last_used=? WHERE id=?", (time.time(), k["id"]))
        return k


def key_user(bearer: str | None, workspace_id: str | None = None):
    """The person behind a key, as a users row in one workspace. A workspace-scoped key answers only for its own
    workspace. An account key answers for the workspace asked for, else the person's first workspace (owners first)."""
    k = key_row(bearer)
    if not k:
        return None
    if k["workspace_id"] != ACCOUNT:
        if workspace_id and workspace_id != k["workspace_id"]:
            return None
        target = k["workspace_id"]
    elif workspace_id:
        target = workspace_id
    else:
        m = memberships(k["email"])
        if not m:
            return None
        target = m[0]["id"]
    with db.conn() as c:
        return c.execute("SELECT * FROM users WHERE workspace_id=? AND email=?", (target, k["email"])).fetchone()


# ---- identity headers handed to tools -----------------------------------------

def sign_identity(signing_key: str, email: str, tier: str, labels: str, tool: str, ts: str) -> str:
    """HMAC over "email|tier|labels|tool|ts". Tools verify this with BOATHOUSE_SIGNING_KEY."""
    msg = "|".join([email, tier, labels, tool, ts]).encode()
    return hmac.new(signing_key.encode(), msg, hashlib.sha256).hexdigest()
