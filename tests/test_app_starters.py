"""Executable starter behavior, source safety and server authorization."""
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tarfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, cwd=None):
    return subprocess.run([sys.executable, str(ROOT / "cli/bh"), *args], cwd=cwd, text=True, capture_output=True)


@pytest.mark.parametrize("kind", ["team", "static"])
def test_init_offline_complete_and_does_not_overwrite(tmp_path, kind):
    target = tmp_path / "my-app"
    r = cli("init", str(target), "--template", kind, "--name", "My useful app")
    assert r.returncode == 0, r.stderr
    meta = json.loads((target / "boathouse.json").read_text())
    assert meta == {"schemaVersion": 1, "slug": "my-app", "name": "My useful app"}
    assert (target / "AGENTS.md").is_file()
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    r = cli("init", str(target), "--template", "static")
    assert r.returncode != 0 and "Nothing changed" in r.stderr
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}
    assert (target / ("app.py" if kind == "team" else "index.html")).is_file()


def test_init_conflict_symlink_and_invalid_slug_create_nothing(tmp_path):
    (tmp_path / "README.md").write_text("keep my work")
    assert cli("init", ".", "--slug", "my-app", cwd=tmp_path).returncode != 0
    assert not (tmp_path / "app.py").exists()
    link = tmp_path / "link"; link.symlink_to(tmp_path, target_is_directory=True)
    assert cli("init", str(link)).returncode != 0
    bad = tmp_path / "bad"
    assert cli("init", str(bad), "--slug", "../outside").returncode != 0 and not bad.exists()


def test_embedded_sources_match_and_preview_data_is_not_uploaded(tmp_path):
    module = runpy.run_path(str(ROOT / "cli/bh"))
    for kind, files in module["STARTER_FILES"].items():
        for name, text in files.items():
            assert text == (ROOT / "templates" / kind / name).read_text()
    target = tmp_path / "my-app"; assert cli("init", str(target)).returncode == 0
    (target / ".local-data").mkdir(); (target / ".local-data/notes.sqlite3").write_text("private notes")
    (target / ".env").write_text("SECRET=not-for-upload")
    with tarfile.open(fileobj=io.BytesIO(module["pack"](target))) as archive:
        names = archive.getnames()
    assert "app.py" in names and "boathouse.json" in names
    assert not any(".local-data" in n or n == ".env" or n.endswith(".sqlite3") for n in names)


@pytest.fixture
def notebook(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bh_starter_probe", ROOT / "templates/team/app.py")
    app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
    monkeypatch.setattr(app, "KEY", b"synthetic-only-starter-test")
    monkeypatch.setattr(app, "TOOL", "test-notebook")
    monkeypatch.setattr(app, "DATA", tmp_path)
    with app.database() as c:c.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, author TEXT NOT NULL, body TEXT NOT NULL)")
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    def headers(user="editor@example.test", tier="editor", stamp=None):
        stamp = str(int(time.time())) if stamp is None else stamp
        values = {"User": user, "Tier": tier, "Labels": "", "Ts": stamp}
        values["Sig"] = app.signature("|".join([user, tier, "", app.TOOL, stamp]))
        return {"X-Boathouse-" + k:v for k,v in values.items()}
    def request(path="/", h=None, fields=None):
        h = dict(h or {})
        data = None if fields is None else urlencode(fields).encode()
        if fields is not None:h["Content-Type"] = "application/x-www-form-urlencoded"
        try:r = urlopen(Request(base + path, data=data, headers=h), timeout=5)
        except HTTPError as e:r = e
        return r.status, r.read().decode()
    yield app, headers, request
    server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_notebook_rejects_unsigned_tampered_stale_and_wrong_tool(notebook):
    app, headers, req = notebook
    assert req()[0] == 403
    assert req("/healthz")[0] == 200
    forged = headers(); forged["X-Boathouse-Tier"] = "admin"
    assert req(h=forged)[0] == 403
    assert req(h=headers(stamp=str(int(time.time()) - 600)))[0] == 403
    assert req(h=headers(stamp="not-a-time"))[0] == 403
    forged = headers(); forged["X-Boathouse-Sig"] = app.signature("editor@example.test|editor||another-app|" + forged["X-Boathouse-Ts"])
    assert req(h=forged)[0] == 403


def test_notebook_enforces_viewers_csrf_and_persists_escaped_notes(notebook):
    app, headers, req = notebook
    viewer = headers(tier="viewer")
    assert req(h=viewer)[0] == 200
    assert req("/notes", viewer, {"note":"forbidden", "csrf":app.csrf("editor@example.test")})[0] == 403
    assert req("/notes", headers(), {"note":"forbidden"})[0] == 403
    assert req("/notes", headers(), {"note":"forbidden", "csrf":app.csrf("someone-else@example.test")})[0] == 403
    note = "<script>alert('no')</script> team update"
    status, text = req("/notes", headers(), {"note":note, "csrf":app.csrf("editor@example.test")})
    assert status == 200 and "&lt;script&gt;" in text and "<script>" not in text
    with app.database() as c:assert c.execute("SELECT body FROM notes").fetchall() == [(note,)]
    assert "team update" in req(h=viewer)[1]
    assert req("/notes", headers(), {"note":"x"*1001,"csrf":app.csrf("editor@example.test")})[0] == 400


def test_preview_cannot_be_enabled_with_host_credentials(tmp_path):
    env = dict(os.environ, BOATHOUSE_TOOL="synthetic-hosted-tool", BOATHOUSE_SIGNING_KEY="synthetic")
    r = subprocess.run([sys.executable, str(ROOT / "templates/team/app.py"), "--local"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "cannot run with hosted" in r.stderr
