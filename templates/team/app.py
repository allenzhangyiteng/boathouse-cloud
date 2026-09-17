"""A shared team notebook. Python standard library; no third-party dependencies.

Local preview: python3 app.py --local (loopback only; synthetic identity).
Boat House supplies PORT, DATA_DIR, BOATHOUSE_TOOL and BOATHOUSE_SIGNING_KEY.
"""
import argparse
import hashlib
import hmac
import html
import os
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

LOCAL = False
KEY = b""
TOOL = ""
DATA = Path(".local-data")


def signature(message):
    return hmac.new(KEY, message.encode(), hashlib.sha256).hexdigest()


def identity(headers):
    if LOCAL:
        return "local@example.test", "admin"
    user, tier, labels, ts, sig = [headers.get("X-Boathouse-" + k, "") for k in ("User", "Tier", "Labels", "Ts", "Sig")]
    try:
        fresh = abs(time.time() - int(ts)) <= 300
    except ValueError:
        fresh = False
    expected = signature("|".join([user, tier, labels, TOOL, ts]))
    if not fresh or tier not in ("viewer", "editor", "admin") or not hmac.compare_digest(expected, sig):
        return None
    return user, tier


def csrf(user):
    stamp = str(int(time.time()))
    return stamp + "." + signature("form|" + user + "|" + stamp)


def valid_csrf(user, token):
    try:
        stamp, sig = token.split(".", 1)
        return 0 <= time.time() - int(stamp) <= 3600 and hmac.compare_digest(sig, signature("form|" + user + "|" + stamp))
    except (ValueError, TypeError):
        return False


def database():
    return sqlite3.connect(DATA / "notes.sqlite3", timeout=10)


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, content_type="text/html; charset=utf-8"):
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/healthz":
            return self.respond(200, "ok", "text/plain")
        if self.path != "/":
            return self.respond(404, "Not found")
        who = identity(self.headers)
        if who is None:
            return self.respond(403, "Open this app through its Boat House address.")
        user, tier = who
        with database() as c:
            notes = c.execute("SELECT author, body FROM notes ORDER BY id DESC LIMIT 50").fetchall()
        entries = "".join("<article><p>" + html.escape(body) + "</p><small>" + html.escape(author or "Guest") + "</small></article>" for author, body in notes)
        form = ('<form method="post" action="/notes"><input type="hidden" name="csrf" value="' + csrf(user) + '"><label for="note">What should your team know?</label><textarea id="note" name="note" maxlength="1000" required></textarea><button>Add a note</button></form>') if tier in ("editor", "admin") else "<p>You have view-only access.</p>"
        title = "Local preview" if LOCAL else "Team notebook"
        self.respond(200, '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Team notebook</title><style>body{font:17px system-ui;max-width:680px;margin:60px auto;padding:24px;color:#17282e;background:#f6f8f7}h1{font-size:40px;letter-spacing:-1.5px}article,form{background:white;padding:24px;border:1px solid #dce4e2;border-radius:16px;margin:16px 0}article p{white-space:pre-wrap}small{color:#536767}textarea{box-sizing:border-box;display:block;width:100%;min-height:90px;margin:12px 0;padding:12px;font:inherit;border:1px solid #b8c9c3;border-radius:8px}button{background:#176454;color:white;border:0;border-radius:8px;padding:12px 20px;font:inherit}a{color:#176454}</style><main><small>' + title + '</small><h1>A place to work together.</h1><p>Signed in as ' + html.escape(user or "Guest") + ' · ' + html.escape(tier) + '</p>' + form + (entries or '<p>No notes yet. Add the first one.</p>') + '</main></html>')

    def do_POST(self):
        if self.path != "/notes":
            return self.respond(404, "Not found")
        who = identity(self.headers)
        if who is None or who[1] not in ("editor", "admin"):
            return self.respond(403, "Editor access is required.")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.respond(400, "Invalid request")
        if not 0 < size <= 16384:
            return self.respond(413, "This note is too large.")
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            return self.respond(415, "Use the note form.")
        try:
            fields = parse_qs(self.rfile.read(size).decode(), max_num_fields=4)
        except (UnicodeError, ValueError):
            return self.respond(400, "Invalid form")
        if not valid_csrf(who[0], fields.get("csrf", [""])[0]):
            return self.respond(403, "Reload the page and try again.")
        note = fields.get("note", [""])[0].strip()
        if not note or len(note) > 1000:
            return self.respond(400, "Write a note of 1 to 1,000 characters.")
        with database() as c:
            c.execute("INSERT INTO notes (author, body) VALUES (?, ?)", (who[0], note))
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass  # Do not log user identities or note contents.


def main():
    global LOCAL, KEY, TOOL, DATA
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="synthetic login, binds only to 127.0.0.1")
    args = parser.parse_args()
    LOCAL = args.local
    if LOCAL and (os.environ.get("BOATHOUSE_TOOL") or os.environ.get("BOATHOUSE_SIGNING_KEY")):
        parser.error("Local preview cannot run with hosted Boat House credentials.")
    if not LOCAL and not all(os.environ.get(k) for k in ("BOATHOUSE_SIGNING_KEY", "BOATHOUSE_TOOL", "DATA_DIR")):
        parser.error("Use --local to preview, or deploy with bh deploy.")
    KEY = secrets.token_bytes(32) if LOCAL else os.environ["BOATHOUSE_SIGNING_KEY"].encode()
    TOOL = "local" if LOCAL else os.environ["BOATHOUSE_TOOL"]
    DATA = Path(".local-data" if LOCAL else os.environ["DATA_DIR"])
    DATA.mkdir(parents=True, exist_ok=True)
    with database() as c:
        c.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, author TEXT NOT NULL, body TEXT NOT NULL)")
    host, port = ("127.0.0.1" if LOCAL else "0.0.0.0"), int(os.environ.get("PORT", "8080"))
    print(f"Team notebook listening on http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
