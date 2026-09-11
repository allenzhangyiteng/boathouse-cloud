"""bh restore: put a tool's database and files back the way a nightly backup has them.

Backups sit on the box at /var/lib/boathouse/backups/<YYYY-MM-DD>/ (deploy/backup.sh, 03:35 UTC):
  t_<slug>.sql.gz        pg_dump --clean of the tool's database
  bh-<slug>-data.tgz     the tool's /data volume
Before anything is replaced, the current database and files are written under STATE_DIR/pre-restore/,
so a restore is itself undoable. The pieces that need the box are module functions so tests can stand in.
"""
import gzip
import os
import pathlib
import re
import subprocess
import shutil
import tempfile
import time

from . import config, db, deploy, export, resources


def points(tool) -> list[dict]:
    """Every dated backup that holds something of this tool, newest first."""
    out = []
    if not config.BACKUP_DIR.is_dir():
        return out
    for d in sorted(config.BACKUP_DIR.iterdir(), reverse=True):
        if not d.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name):
            continue
        if (d/".inprogress").exists():
            continue
        dbf, df = d / f"{deploy.db_name(tool)}.sql.gz", d / f"{deploy.vol_name(tool)}.tgz"
        if not df.exists():
            df=d/f"bh-{deploy.resource_key(tool)}-data.tgz"
        if dbf.exists() or df.exists():
            out.append({"date": d.name, "database": dbf.exists(), "files": df.exists(),
                        "database_bytes": dbf.stat().st_size if dbf.exists() else 0,
                        "files_bytes": df.stat().st_size if df.exists() else 0, "files_name": df.name})
    return out


def snapshot(tool: dict) -> pathlib.Path:
    """What the tool holds right now, kept beside the state so a restore can be undone by hand."""
    d = config.STATE_DIR / "pre-restore" / f"{deploy.resource_key(tool)}-{time.time_ns()}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    d.mkdir(parents=True, exist_ok=True)
    if config.RESOURCE_GUARD:
        from . import resources
        with resources.host_operation():export.stream_snapshot(tool,d)
        return d
    (d / "database.sql").write_bytes(export.dump_database(tool))
    blob = export.data_archive(tool)
    if blob:
        (d / "data.tar").write_bytes(blob)
    return d


def apply_database(tool: dict, dump_gz: pathlib.Path):
    """Empty the tool's schema and replay the dump, as the tool's own role so it owns what comes back."""
    name = deploy.db_name(tool)
    env = {**os.environ, "PGPASSWORD": tool["db_password"]}
    def psql(args, stdin=None):
        r = subprocess.run(["psql", "-h", config.PG_HOST, "-U", name, "-d", name, "-q", "-v", "ON_ERROR_STOP=1", *args],
                           env=env, stdin=stdin, capture_output=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError("psql failed: " + r.stderr.decode(errors="replace")[-400:])
    # Validate/decompress before changing the database. A large backup must not
    # occupy the control service's memory or consume the host's emergency space.
    maximum=2*resources.measure(tool)['limit_bytes']+config.MAX_SOURCE_HISTORY_BYTES if config.RESOURCE_GUARD else None
    with tempfile.TemporaryFile(dir=config.STATE_DIR) as prepared, gzip.open(dump_gz,'rb') as source:
        total=0
        while chunk:=source.read(1024*1024):
            total+=len(chunk)
            if maximum is not None and (total>maximum or shutil.disk_usage(config.STATE_DIR).free<8*1024**3+len(chunk)):
                raise RuntimeError('This database backup exceeds safe restore capacity. Contact support; the database has not been changed.')
            prepared.write(chunk)
        prepared.seek(0)
        psql(["-c", "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"])
        psql(["-f", "-"], stdin=prepared)


def apply_files(tool, tgz_host_path: str):
    """Empty the volume and unpack the backup into it with a throwaway container. The path is the host's,
    because the docker daemon is the one mounting it."""
    deploy.client().containers.run(
        "alpine", ["sh", "-c", "rm -rf /v/* /v/.[!.]* 2>/dev/null; tar xzf /b.tgz -C /v"], remove=True, mem_limit="128m", memswap_limit="128m", nano_cpus=500000000, pids_limit=64,
        volumes={deploy.vol_name(tool): {"bind": "/v", "mode": "rw"}, tgz_host_path: {"bind": "/b.tgz", "mode": "ro"}})


def restore(tool: dict, ws: dict, date: str) -> dict:
    with deploy.operation(tool), resources.host_operation():
        with db.conn() as c:
            current = c.execute("SELECT * FROM tools WHERE id=?", (tool["id"],)).fetchone()
        if current is None:
            raise ValueError("tool was deleted while this operation was waiting")
        return _restore_locked(dict(current), ws, date)


def _restore_locked(tool, ws, date):
    pt = next((p for p in points(tool) if p["date"] == date), None)
    if not pt:
        raise ValueError(f"no backup of {tool['slug']} dated {date}")
    snap = snapshot(tool)
    deploy.stop(tool)
    done = {"date": date, "snapshot": str(snap), "database": False, "files": False}
    try:
        if pt["database"]:
            apply_database(tool, config.BACKUP_DIR / date / f"{deploy.db_name(tool)}.sql.gz")
            done["database"] = True
        if pt["files"]:
            apply_files(tool, f"{config.BACKUP_DIR_HOST}/{date}/{pt.get('files_name',deploy.vol_name(tool)+'.tgz')}")
            done["files"] = True
    finally:
        if tool.get("current_release_id"):
            deploy._restart(tool, ws)
    return done
