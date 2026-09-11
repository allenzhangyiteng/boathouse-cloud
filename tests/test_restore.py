"""bh restore: plan first, then snapshot, stop, database, files, start."""
import gzip
import os
import sys
import tempfile
import time

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PUBLIC_IP="203.0.113.4", BH_PG_PASSWORD="x",
                  BH_OWNERS="owner@starter.test", BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-restore-"),
                  BH_BACKUP_DIR=tempfile.mkdtemp(prefix="bh-backups-"), BH_BACKUP_HOST="/host/backups")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, config, db, deploy, main, restore  # noqa: E402

PLAT = "platform.test"
API = {"host": f"api.{PLAT}"}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver") as cl:
        yield cl


@pytest.fixture(scope="module")
def setup(client):
    # config is imported once for the whole suite, so point the backup folders at this module's temp dirs directly
    from pathlib import Path
    config.BACKUP_DIR = Path(os.environ["BH_BACKUP_DIR"])
    config.BACKUP_DIR_HOST = "/host/backups"
    main.create_workspace("Restore Co", "owner@restore.test")
    wsid = db.workspace("restore-co")["id"]
    with db.conn() as c:
        # Explicitly preserved legacy identity: historical backups keep their names.
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by, current_release_id, resource_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ("t_r1", wsid, "ledger", "Ledger", "k", "pw", time.time(), "owner@restore.test", "r_x", "ledger"))
    for day, files in (("2026-09-05", ("db",)), ("2026-09-06", ("db", "data")), ("not-a-date", ("db", "data"))):
        d = config.BACKUP_DIR / day; d.mkdir()
        if "db" in files:
            (d / "t_ledger.sql.gz").write_bytes(gzip.compress(b"DROP TABLE IF EXISTS rows; CREATE TABLE rows ();\n"))
        if "data" in files:
            (d / "bh-ledger-data.tgz").write_bytes(b"x" * 100)
    return {"wsid": wsid, "key": auth.create_project_key(wsid, "owner@restore.test", "t")}


def test_points_list_newest_first_and_only_real_dates(setup, client):
    r = client.get("/api/tools/ledger/restore-points", headers={**API, "Authorization": f"Bearer {setup['key']}"})
    assert r.status_code == 200
    assert [(p["date"], p["database"], p["files"]) for p in r.json()] == [("2026-09-06", True, True), ("2026-09-05", True, False)]


def test_plan_changes_nothing_and_confirm_runs_the_steps_in_order(setup, client, monkeypatch):
    calls = []
    monkeypatch.setattr(restore, "snapshot", lambda tool: calls.append("snapshot") or config.STATE_DIR / "pre-restore" / "x")
    monkeypatch.setattr(deploy, "stop", lambda tool: calls.append(f"stop:{tool['slug']}"))
    monkeypatch.setattr(restore, "apply_database", lambda tool, path: calls.append(f"db:{path.name}"))
    monkeypatch.setattr(restore, "apply_files", lambda slug, host: calls.append(f"files:{host}"))
    monkeypatch.setattr(deploy, "_restart", lambda tool, ws: calls.append("restart"))
    h = {**API, "Authorization": f"Bearer {setup['key']}"}
    r = client.post("/api/tools/ledger/restore", headers=h, json={})
    assert r.status_code == 200 and r.json()["confirm"] is False and r.json()["plan"]["date"] == "2026-09-06" and calls == []
    r = client.post("/api/tools/ledger/restore", headers=h, json={"date": "2026-09-05", "confirm": True})
    assert r.status_code == 200 and r.json()["database"] is True and r.json()["files"] is False
    assert calls == ["snapshot", "stop:ledger", "db:t_ledger.sql.gz", "restart"]
    calls.clear()
    r = client.post("/api/tools/ledger/restore", headers=h, json={"date": "2026-09-06", "confirm": True})
    assert r.status_code == 200 and calls == ["snapshot", "stop:ledger", "db:t_ledger.sql.gz", "files:/host/backups/2026-09-06/bh-ledger-data.tgz", "restart"]
    assert client.post("/api/tools/ledger/restore", headers=h, json={"date": "2026-09-01", "confirm": True}).status_code == 404


def test_member_cannot_restore(setup, client):
    client.post("/api/users", headers={**API, "Authorization": f"Bearer {setup['key']}"}, json={"email": "kira@restore.test", "role": "member"})
    mkey = auth.create_project_key(setup["wsid"], "kira@restore.test", "t")
    assert client.get("/api/tools/ledger/restore-points", headers={**API, "Authorization": f"Bearer {mkey}"}).status_code == 403


def test_large_or_invalid_restore_is_rejected_before_schema_changes(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(config,'RESOURCE_GUARD',True)
    monkeypatch.setattr(config,'STATE_DIR',tmp_path)
    monkeypatch.setattr(config,'MAX_SOURCE_HISTORY_BYTES',0)
    monkeypatch.setattr(restore.resources,'measure',lambda tool:{'limit_bytes':1024})
    monkeypatch.setattr(restore.shutil,'disk_usage',lambda path:SimpleNamespace(free=20*1024**3))
    calls=[]
    monkeypatch.setattr(restore.subprocess,'run',lambda *a,**kw:calls.append(a))
    backup=tmp_path/'large.gz';backup.write_bytes(gzip.compress(b'x'*4096))
    with pytest.raises(RuntimeError,match='has not been changed'):restore.apply_database({'resource_key':'probe-test','db_password':'synthetic'},backup)
    assert not calls
    backup.write_bytes(b'not gzip')
    with pytest.raises(gzip.BadGzipFile):restore.apply_database({'resource_key':'probe-test','db_password':'synthetic'},backup)
    assert not calls
