#!/usr/bin/env python3
"""Create a private control-state archive with a consistent SQLite snapshot.

The archive is sensitive: it includes the master recovery key. This is not
encryption. Store it only in protected backup storage with restricted access.
"""
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile


def snapshot(state: Path, destination: Path):
    state = state.resolve()
    database = state / "boathouse.sqlite3"
    if not database.is_file():
        raise RuntimeError("control database is missing")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".state-snapshot-", dir=destination.parent) as folder:
        staged = Path(folder) / state.name
        shutil.copytree(state, staged, ignore=shutil.ignore_patterns(
            "boathouse.sqlite3", "boathouse.sqlite3-wal", "boathouse.sqlite3-shm", "builds", "locks"))
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
            target = sqlite3.connect(staged / database.name)
            try:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("control database backup integrity check failed")
            finally:
                target.close()
        temporary = Path(folder) / "control-state.tgz"
        with tarfile.open(temporary, "w:gz") as archive:
            archive.add(staged, arcname=state.name)
        temporary.chmod(0o600)
        os.replace(temporary, destination)


if __name__ == "__main__":
    snapshot(Path(sys.argv[1]), Path(sys.argv[2]))
