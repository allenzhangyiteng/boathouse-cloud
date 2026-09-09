"""bh export: the whole tool as one archive, without a box."""
import io
import os
import sys
import tarfile
import tempfile
import time

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PUBLIC_IP="203.0.113.4", BH_PG_PASSWORD="x",
                  BH_OWNERS="owner@starter.test", BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-export-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, config, db, export, main  # noqa: E402

PLAT = "platform.test"
API = {"host": f"api.{PLAT}"}


def tgz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            ti = tarfile.TarInfo(name); ti.size = len(body); tf.addfile(ti, io.BytesIO(body))
    return buf.getvalue()


def tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for name, body in files.items():
            ti = tarfile.TarInfo(name); ti.size = len(body); tf.addfile(ti, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver") as cl:
        yield cl


@pytest.fixture(scope="module")
def setup(client):
    ws = main.create_workspace("Export Co", "owner@export.test")
    wsid = db.workspace("export-co")["id"]
    src_dir = config.SOURCE_DIR / "tracker"; src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "1.tar.gz"; src.write_bytes(tgz({"Dockerfile": b"FROM scratch\n", "app.py": b"print(1)\n", "./.dockerignore": b"*.pyc\n"}))
    with db.conn() as c:
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_1", wsid, "tracker", "Tracker", "k", "pw", time.time(), "owner@export.test"))
        c.execute("INSERT INTO releases (id, tool_id, seq, image, status, note, log, created, created_by, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ("r_1", "t_1", 1, "img", "live", None, None, time.time(), "owner@export.test", str(src)))
        c.execute("UPDATE tools SET current_release_id=? WHERE id=?", ("r_1", "t_1"))
    return {"ws": ws, "wsid": wsid}


def test_export_has_source_database_data_and_readme(setup, client, monkeypatch):
    monkeypatch.setattr(export, "dump_database", lambda slug: b"-- PostgreSQL database dump\nCREATE TABLE payments ();\n")
    monkeypatch.setattr(export, "data_archive", lambda slug: tar({"data/uploads/receipt.pdf": b"%PDF", "data/notes.txt": b"hi"}))
    key = auth.create_project_key(setup["wsid"], "owner@export.test", "t")
    c = client
    if True:
        r = c.get("/api/tools/tracker/export", headers={**API, "Authorization": f"Bearer {key}"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/gzip")
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
            names = set(tf.getnames())
            assert {"source/Dockerfile", "source/app.py", "source/.dockerignore", "database.sql", "data/uploads/receipt.pdf", "data/notes.txt", "README.txt"} <= names   # the dot survives
            assert b"CREATE TABLE payments" in tf.extractfile("database.sql").read()
            readme = tf.extractfile("README.txt").read().decode()
            assert "release #1" in readme and "Secrets" in readme and "psql tracker" in readme
        # a member who is not admin on the tool gets nothing
        c.post("/api/users", headers={**API, "Authorization": f"Bearer {key}"}, json={"email": "kira@export.test", "role": "member"})
        mkey = auth.create_project_key(setup["wsid"], "kira@export.test", "t")
        assert c.get("/api/tools/tracker/export", headers={**API, "Authorization": f"Bearer {mkey}"}).status_code == 403


def test_export_without_a_container_or_source_still_works(setup, client, monkeypatch):
    monkeypatch.setattr(export, "dump_database", lambda slug: b"-- empty\n")
    monkeypatch.setattr(export, "data_archive", lambda slug: None)
    with db.conn() as c:
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_2", setup["wsid"], "bare", "Bare", "k", "pw", time.time(), "owner@export.test"))
    key = auth.create_project_key(setup["wsid"], "owner@export.test", "t")
    c = client
    if True:
        r = c.get("/api/tools/bare/export", headers={**API, "Authorization": f"Bearer {key}"})
        assert r.status_code == 200
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
            assert set(tf.getnames()) == {"database.sql", "README.txt"}
