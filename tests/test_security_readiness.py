"""Regression checks for control-page protections and consistent private backups."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile

os.environ.setdefault("BH_DOMAIN", "bhtest-platform.com")
os.environ.setdefault("BH_PG_PASSWORD", "test-only")
os.environ.setdefault("BH_STATE_DIR", tempfile.mkdtemp(prefix="bh-security-"))
os.environ.setdefault("BH_METER", "0")
sys.path.insert(0, str(Path(__file__).parents[1] / "control"))
from fastapi.testclient import TestClient
from app import config, main


def test_control_pages_cannot_be_framed():
    client = TestClient(main.app)
    response = client.get("/api/health", headers={"host": config.API_HOST})
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_backup_includes_committed_wal_and_is_private(tmp_path):
    path = Path(__file__).parents[1] / "deploy" / "snapshot-state.py"
    spec = importlib.util.spec_from_file_location("snapshot_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / "state"; state.mkdir()
    live = sqlite3.connect(state / "boathouse.sqlite3")
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE proof (value TEXT)")
        live.execute("INSERT INTO proof VALUES ('committed in WAL')"); live.commit()
        (state / "master.key").write_text("synthetic-recovery-key")
        destination = tmp_path / "backups" / "control-state.tgz"
        module.snapshot(state, destination)
        assert destination.stat().st_mode & 0o777 == 0o600
        restored = tmp_path / "restored"; restored.mkdir()
        with tarfile.open(destination) as archive:
            assert "state/boathouse.sqlite3-wal" not in archive.getnames()
            archive.extractall(restored, filter="data")
        with sqlite3.connect(restored / "state/boathouse.sqlite3") as check:
            assert check.execute("SELECT value FROM proof").fetchone()[0] == "committed in WAL"
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        live.close()
