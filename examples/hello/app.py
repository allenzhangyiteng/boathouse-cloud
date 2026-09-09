"""The smallest Boathouse tool: shows who Boathouse says you are, after
verifying the signature so a forged header would be refused."""
import hashlib, hmac, os, html
from http.server import BaseHTTPRequestHandler, HTTPServer

KEY = os.environ["BOATHOUSE_SIGNING_KEY"].encode()
TOOL = os.environ["BOATHOUSE_TOOL"]

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        h = self.headers
        user, tier, labels = h.get("X-Boathouse-User",""), h.get("X-Boathouse-Tier",""), h.get("X-Boathouse-Labels","")
        ts, sig = h.get("X-Boathouse-Ts",""), h.get("X-Boathouse-Sig","")
        expect = hmac.new(KEY, "|".join([user, tier, labels, TOOL, ts]).encode(), hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(expect, sig)
        body = f"<h1>hello, {html.escape(user)}</h1><p>tier: {html.escape(tier)} · labels: {html.escape(labels) or '-'} · signature {'verified' if ok else 'INVALID'} · db: {'yes' if os.environ.get('DATABASE_URL') else 'no'}</p>"
        self.send_response(200 if ok else 403); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(body.encode())
    def log_message(self, *a): pass

HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()
