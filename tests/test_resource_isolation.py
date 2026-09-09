"""Tenant isolation, legacy migration, and concurrent deployment regressions."""
import asyncio
import io
import os
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

os.environ.setdefault("BH_DOMAIN", "platform.test")
os.environ.setdefault("BH_PG_PASSWORD", "test-only")
os.environ.setdefault("BH_STATE_DIR", tempfile.mkdtemp(prefix="bh-isolation-import-"))
os.environ.setdefault("BH_METER", "0")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from docker.errors import NotFound
from app import auth, billing, bootstrap, config, db, deploy, export, main, restore


@pytest.fixture
def state(tmp_path, monkeypatch):
    for attr, val in {"STATE_DIR": tmp_path, "DB_PATH": tmp_path / "state.sqlite3",
                      "SOURCE_DIR": tmp_path / "source", "MASTER_KEY_PATH": tmp_path / "master.key",
                      "BACKUP_DIR": tmp_path / "backups"}.items():
        monkeypatch.setattr(config, attr, val)
    db.init()
    monkeypatch.setattr(deploy, "status", lambda tool: {"state": "running"})
    with db.conn() as c:
        for suffix in ("a", "b"):
            c.execute("INSERT INTO workspaces (id,slug,name,created,balance_cents) VALUES (?,?,?,?,?)",
                      (f"ws_{suffix}", f"tenant-{suffix}", suffix, time.time(), 10000))
            c.execute("INSERT INTO users (id,workspace_id,email,role,created) VALUES (?,?,?,?,?)",
                      (f"u_{suffix}", f"ws_{suffix}", f"{suffix}@test.invalid", "owner", time.time()))
    return tmp_path


def insert_tool(suffix, slug="tracker", resource=None):
    with db.conn() as c:
        tid = db.new_id("t")
        c.execute("INSERT INTO tools (id,workspace_id,slug,name,signing_key,db_password,created,resource_key) VALUES (?,?,?,?,?,?,?,?)",
                  (tid, f"ws_{suffix}", slug, slug, f"sign-{suffix}", f"db-{suffix}", time.time(), resource))
        return dict(c.execute("SELECT * FROM tools WHERE id=?", (tid,)).fetchone())


def tgz(text):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        body = text.encode()
        entry = tarfile.TarInfo("index.html"); entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    return buf.getvalue()


def release(tool, body, base=None):
    return main._do_release(tool, db.workspace_by_id(tool["workspace_id"]), {"email": "a@test.invalid"}, tgz(body), None, None, base=base)


def headers(suffix):
    key = auth.create_project_key(f"ws_{suffix}", f"{suffix}@test.invalid", "isolation-test")
    return {"host": f"api.{config.PLATFORM_DOMAIN}", "Authorization": f"Bearer {key}"}


def fake_releases(monkeypatch):
    monkeypatch.setattr(deploy, "build", lambda tool, seq, context: (deploy.image_tag(tool, seq), "built"))
    monkeypatch.setattr(deploy, "run", lambda tool, tag, ws: "container")
    monkeypatch.setattr(deploy, "prune_images", lambda tool, keep: None)


def test_same_slug_has_distinct_every_resource_and_source_and_gateway(state, monkeypatch):
    a, b = insert_tool("a"), insert_tool("b")
    for naming in (deploy.ctr_name, deploy.net_name, deploy.vol_name, deploy.db_name):
        assert naming(a) != naming(b)
    assert deploy.image_tag(a, 1) != deploy.image_tag(b, 1)
    with pytest.raises(TypeError):
        deploy.ctr_name("tracker")
    fake_releases(monkeypatch)
    assert release(a, "tenant-a")["release"] == release(b, "tenant-b")["release"] == 1
    api = TestClient(main.app)
    for suffix, tool in (("a", a), ("b", b)):
        response = api.get("/api/tools/tracker/source", headers=headers(suffix))
        assert response.status_code == 200
        with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
            assert archive.extractfile("index.html").read() == f"tenant-{suffix}".encode()
        with db.conn() as c:
            c.execute("UPDATE tools SET public=1 WHERE id=?", (tool["id"],))
        routed = api.get("/internal/authz", headers={"host": f"tracker.tenant-{suffix}.{config.PLATFORM_DOMAIN}"})
        assert routed.headers["x-boathouse-upstream"] == f"{deploy.ctr_name(tool)}:8080"


def test_retained_data_namespace_credentials_survive_recreate_and_purge_does_not_reuse(state, monkeypatch):
    a, b = insert_tool("a"), insert_tool("b")
    removed = []
    monkeypatch.setattr(deploy, "remove", lambda tool, purge: removed.append((tool["resource_key"], purge)))
    api = TestClient(main.app); h = headers("a")
    assert api.delete("/api/tools/tracker", headers=h).status_code == 200
    assert api.post("/api/tools", headers=h, json={"slug": "tracker"}).status_code == 200
    revived = dict(main._tool_by_slug("ws_a", "tracker"))
    assert revived["id"] != a["id"]
    for field in ("resource_key", "db_password", "signing_key"):
        assert revived[field] == a[field]
    assert main._source_path(revived, 1) != main._source_path(a, 1)
    assert api.delete("/api/tools/tracker?purge=true", headers=h).status_code == 200
    assert api.post("/api/tools", headers=h, json={"slug": "tracker"}).status_code == 200
    fresh = dict(main._tool_by_slug("ws_a", "tracker"))
    assert fresh["resource_key"] not in (a["resource_key"], b["resource_key"])
    assert removed == [(a["resource_key"], False), (a["resource_key"], True)]


def test_validation_has_no_partial_creation(state):
    api = TestClient(main.app); h = headers("a")
    for body in ({"slug": "tracker", "default_tier": "typo"}, {"slug": "tracker", "default_access": "typo"}):
        assert api.post("/api/tools", headers=h, json=body).status_code == 422
        assert main._tool_by_slug("ws_a", "tracker") is None
    assert api.post("/api/tools", headers=h, json={"slug": "tracker"}).status_code == 200


def test_concurrent_stale_base_only_one_release_wins(state, monkeypatch):
    tool = insert_tool("a"); fake_releases(monkeypatch)
    release(tool, "original")
    started = threading.Event(); proceed = threading.Event()
    def build(tool, seq, context):
        started.set(); assert proceed.wait(5)
        return deploy.image_tag(tool, seq), "built"
    monkeypatch.setattr(deploy, "build", build)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(release, tool, "first change", 1)
        assert started.wait(5)
        second = pool.submit(release, tool, "stale change", 1)
        proceed.set()
        assert first.result()["release"] == 2
        with pytest.raises(HTTPException) as failure:
            second.result()
        assert failure.value.status_code == 409 and failure.value.detail["current"] == 2
    with db.conn() as c:
        assert c.execute("SELECT count(*) FROM releases WHERE tool_id=?", (tool["id"],)).fetchone()[0] == 2
    with pytest.raises(HTTPException):
        release(tool, "stale initial push", 0)


def test_prune_failure_does_not_mark_live_release_failed(state, monkeypatch):
    tool = insert_tool("a"); fake_releases(monkeypatch)
    def broken(*args): raise RuntimeError("docker prune unavailable")
    monkeypatch.setattr(deploy, "prune_images", broken)
    assert release(tool, "live")["release"] == 1
    with db.conn() as c:
        assert c.execute("SELECT status FROM releases WHERE tool_id=?", (tool["id"],)).fetchone()[0] == "live"


def test_shared_image_id_never_removes_another_tenants_tag(state, monkeypatch):
    a, b = insert_tool("a"), insert_tool("b")
    tags = [deploy.image_tag(a, 1), deploy.image_tag(a, 2), deploy.image_tag(b, 1)]
    removed = []
    docker = SimpleNamespace(images=SimpleNamespace(list=lambda **kwargs: [SimpleNamespace(id="same-sha", tags=tags)],
                                                    remove=lambda tag, **kwargs: removed.append(tag)))
    monkeypatch.setattr(deploy, "client", lambda: docker)
    deploy.prune_images(a, {tags[1]})
    assert removed == [tags[0]]


def test_export_backup_lookup_and_meter_use_persisted_identity(state, monkeypatch):
    a, b = insert_tool("a"), insert_tool("b")
    folder = config.BACKUP_DIR / "2026-09-08"; folder.mkdir(parents=True)
    (folder / f"{deploy.db_name(a)}.sql.gz").write_bytes(b"only-a")
    (folder / f"{deploy.vol_name(b)}.tgz").write_bytes(b"only-b")
    assert restore.points(a)[0]["database"] and not restore.points(a)[0]["files"]
    assert restore.points(b)[0]["files"] and not restore.points(b)[0]["database"]
    calls = []
    monkeypatch.setattr(export, "dump_database", lambda tool: calls.append(("db", tool["resource_key"])) or b"sql")
    monkeypatch.setattr(export, "data_archive", lambda tool: calls.append(("files", tool["resource_key"])) or None)
    path = export.build(a, dict(db.workspace_by_id("ws_a")), None, None)
    path.unlink()
    assert calls == [("db", a["resource_key"]), ("files", a["resource_key"])]
    checked = []
    monkeypatch.setattr(deploy, "status", lambda tool: checked.append(tool["resource_key"]) or {"state": "absent"})
    monkeypatch.setattr(billing, "_settle", lambda ws: None)
    billing.meter_once("2026-09-08")
    assert set(checked) == {a["resource_key"], b["resource_key"]}


def legacy_database(path, duplicates=False):
    with sqlite3.connect(path) as c:
        c.executescript(db.SCHEMA)
        for suffix in ("a", "b"):
            c.execute("INSERT INTO workspaces (id,slug,name,created) VALUES (?,?,?,0)", (f"ws_{suffix}", suffix, suffix))
        c.execute("INSERT INTO tools (id,workspace_id,slug,name,signing_key,db_password,created) VALUES ('old','ws_a','tracker','tracker','k','p',0)")
        if duplicates:
            c.execute("INSERT INTO tool_keepsakes VALUES ('ws_b','tracker','[]','[]',0)")


def test_legacy_migration_preserves_explicit_names_new_names_never_claim_them(state, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.sqlite3"; legacy_database(legacy)
    monkeypatch.setattr(config, "DB_PATH", legacy)
    assert db.resource_migration_plan()["safe"]
    db.init(); old = dict(main._tool_by_slug("ws_a", "tracker"))
    assert old["resource_key"] == "tracker" and deploy.ctr_name(old) == "tool-tracker"
    new = insert_tool("b")
    assert new["resource_key"] != "tracker"
    db.init()
    assert dict(main._tool_by_slug("ws_a", "tracker"))["resource_key"] == "tracker"
    with db.conn() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE tools SET resource_key='changed' WHERE id='old'")


def test_ambiguous_legacy_migration_dry_run_and_failure_leave_schema_unchanged(state, tmp_path, monkeypatch):
    legacy = tmp_path / "ambiguous.sqlite3"; legacy_database(legacy, duplicates=True)
    monkeypatch.setattr(config, "DB_PATH", legacy)
    plan = db.resource_migration_plan()
    assert not plan["safe"] and len(plan["conflicts"][0]["owners"]) == 2
    with pytest.raises(RuntimeError, match="multiple workspace owners"):
        db.init()
    with db.conn() as c:
        assert "resource_key" not in {row["name"] for row in c.execute("PRAGMA table_info(tools)")}
        assert "balance_cents" not in {row["name"] for row in c.execute("PRAGMA table_info(workspaces)")}
    assert db.resource_migration_plan() == plan


def test_gateway_strips_only_reserved_cookies_and_signs_real_identity(state):
    tool = insert_tool("a")
    api = TestClient(main.app)
    sid = auth.create_session("ws_a", "a@test.invalid", "A")
    response = api.get("/internal/authz", headers={"host": f"tracker.tenant-a.{config.PLATFORM_DOMAIN}",
        "Cookie": f"app_session=keep; bh_session=duplicate; theme=dark; bh_account=private; bh_session={sid}",
        "X-Boathouse-App-Cookie": "bh_session=attacker", "X-Boathouse-User": "attacker@test.invalid"})
    assert response.status_code == 200
    assert response.headers["x-boathouse-app-cookie"] == "app_session=keep; theme=dark"
    assert response.headers["x-boathouse-user"] == "a@test.invalid"
    assert response.headers["x-boathouse-sig"] == auth.sign_identity(tool["signing_key"], "a@test.invalid", "admin", "", "tracker", response.headers["x-boathouse-ts"])


def test_probe_exception_removes_replacement_and_restores_previous_container(state, monkeypatch):
    from unittest.mock import MagicMock
    tool = insert_tool("a")
    old, replacement = MagicMock(), MagicMock()
    docker = MagicMock()
    def get(name):
        if name == deploy.ctr_name(tool): return old
        raise NotFound("absent")
    docker.containers.get.side_effect = get
    monkeypatch.setattr(deploy, "client", lambda: docker)
    monkeypatch.setattr(deploy, "ensure_network", lambda tool: None)
    monkeypatch.setattr(deploy, "ensure_volume", lambda tool: None)
    monkeypatch.setattr(deploy, "tool_env", lambda tool, ws: {})
    monkeypatch.setattr(deploy, "_start", lambda *args: replacement)
    def fail_probe(*args): raise RuntimeError("Docker network probe failed")
    monkeypatch.setattr(deploy, "_probe", fail_probe)
    with pytest.raises(RuntimeError, match="previous release was restarted"):
        deploy.run(tool, deploy.image_tag(tool, 2), db.workspace_by_id("ws_a"))
    replacement.remove.assert_called_once_with(force=True)
    assert old.rename.call_args.args == (deploy.ctr_name(tool),)
    old.start.assert_called_once()


def test_start_and_remove_target_only_the_selected_tenants_network_and_volume(state, monkeypatch):
    from unittest.mock import MagicMock
    a, b = insert_tool("a"), insert_tool("b")
    docker = MagicMock(); network = MagicMock(); volume = MagicMock(); container = MagicMock()
    docker.images.list.return_value = [SimpleNamespace(id="shared", tags=[deploy.image_tag(a, 1), deploy.image_tag(b, 1)])]
    docker.networks.get.return_value = network
    docker.volumes.get.return_value = volume
    docker.containers.get.return_value = container
    monkeypatch.setattr(deploy, "client", lambda: docker)
    deploy._start(docker, deploy.image_tag(a, 1), a, {"CUSTOM": "value"})
    args = docker.containers.run.call_args.kwargs
    assert args["name"] == deploy.ctr_name(a) and args["network"] == deploy.net_name(a)
    assert set(args["volumes"]) == {deploy.vol_name(a)}
    assert args["labels"]["boathouse.workspace"] == "ws_a"
    deploy.remove(a, purge=False)
    docker.networks.get.assert_called_once_with(deploy.net_name(a))
    docker.volumes.get.assert_not_called()
    docker.images.remove.assert_called_once_with(deploy.image_tag(a, 1), force=True)
    names = [call.args[0] for call in docker.containers.get.call_args_list]
    assert deploy.ctr_name(b) not in names


def test_context_read_is_bounded_and_accepts_exact_limit(monkeypatch):
    monkeypatch.setattr(main, 'MAX_CONTEXT_BYTES', 16)
    class Upload:
        size = None
        def __init__(self, content): self.content, self.requests, self.offset = content, [], 0
        async def read(self, size=-1):
            assert size > 0, 'never read an unbounded body'
            self.requests.append(size)
            out = self.content[self.offset:self.offset+size]
            self.offset += len(out)
            return out
    exact = Upload(b'x'*16)
    assert asyncio.run(main._read_context(exact)) == b'x'*16
    oversized = Upload(b'x'*1000)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(main._read_context(oversized))
    assert failure.value.status_code == 413 and oversized.offset == 17
    declared = Upload(b'x'*1000); declared.size = 1000
    with pytest.raises(HTTPException):
        asyncio.run(main._read_context(declared))
    assert declared.requests == []


def test_oversized_context_does_not_create_a_partial_tool(state, monkeypatch):
    monkeypatch.setattr(main, 'MAX_CONTEXT_BYTES', 16)
    api = TestClient(main.app)
    response = api.post('/api/tools/tracker/deploys', headers=headers('a'),
                        files={'context': ('context.tar.gz', b'x'*17, 'application/gzip')})
    assert response.status_code == 413
    assert main._tool_by_slug('ws_a', 'tracker') is None


def test_purge_removes_only_its_immutable_source_directory(state, monkeypatch):
    a, b = insert_tool('a'), insert_tool('b')
    for tool, body in ((a, b'a-source'), (b, b'b-source')):
        source = main._source_path(tool, 1)
        source.parent.mkdir(parents=True)
        source.write_bytes(body)
    legacy = config.SOURCE_DIR / 'tracker' / '1.tar.gz'
    legacy.parent.mkdir(); legacy.write_bytes(b'legacy-history')
    monkeypatch.setattr(deploy, 'remove', lambda tool, purge: None)
    api = TestClient(main.app)
    assert api.delete('/api/tools/tracker?purge=true', headers=headers('a')).status_code == 200
    assert not main._source_path(a, 1).parent.exists()
    assert main._source_path(b, 1).read_bytes() == b'b-source'
    assert legacy.read_bytes() == b'legacy-history'
    assert api.delete('/api/tools/tracker', headers=headers('b')).status_code == 200
    assert main._source_path(b, 1).read_bytes() == b'b-source'
