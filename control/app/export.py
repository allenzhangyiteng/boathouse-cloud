"""bh export: one tool, whole, as one archive its owner can take anywhere.

  source/        the folder the live release was built from (what `bh pull` fetches), Dockerfile included
  database.sql   the tool's own Postgres database, plain SQL from pg_dump
  data/          everything the tool kept under /data (DATA_DIR)
  README.txt     what is in here and how to run it somewhere else

Secrets set with `bh secrets` are deliberately left out; the README says so.
The two pieces that need the box (pg_dump, the container's /data) are module functions so tests can stand in for them.
"""
import io
import os
import pathlib
import re
import subprocess
import tarfile
import tempfile
import time

from . import config, deploy


def dump_database(tool) -> bytes:
    name = deploy.db_name(tool)
    env = {**os.environ, "PGPASSWORD": config.PG_ADMIN_PASSWORD}
    out = subprocess.run(["pg_dump", "-h", config.PG_HOST, "-U", "postgres", "--no-owner", "--no-privileges", name],
                         env=env, capture_output=True, timeout=900)
    if out.returncode != 0:
        raise RuntimeError("pg_dump failed: " + out.stderr.decode(errors="replace")[-400:])
    return out.stdout


def data_archive(tool) -> bytes | None:
    """A tar of /data from the tool's container, running or stopped. None when there is no container."""
    import docker
    try:
        ctr = deploy.client().containers.get(deploy.ctr_name(tool))
    except docker.errors.NotFound:
        return None
    stream, _ = ctr.get_archive("/data")
    return b"".join(stream)


def _readme(tool: dict, ws: dict, seq: int | None) -> str:
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    rel = f"release #{seq}" if seq else "no release recorded"
    return f"""Boat House export of "{tool['name']}" ({tool['slug']}), workspace {ws['slug']}, made {when}.

source/        the folder the live release was built from ({rel}). It has the Dockerfile.
database.sql   the tool's PostgreSQL database as plain SQL.
               Restore:  createdb {tool['slug']} && psql {tool['slug']} < database.sql
data/          everything the tool kept under /data (its DATA_DIR).

To run it somewhere else:
  1. Build the Dockerfile in source/ and run it with PORT=8080, DATABASE_URL pointing at the restored
     database, and DATA_DIR=/data with the data/ folder mounted there.
  2. On Boat House the gateway tells the tool who is signed in (X-Boathouse-User and X-Boathouse-Tier
     headers, signed with BOATHOUSE_SIGNING_KEY). Elsewhere, put your own login in front and send those
     headers, or change the tool to use a different login.
  3. Secrets set with `bh secrets` are NOT in this archive. Set them again as environment variables.
"""


def _safe(name: str) -> bool:
    return not (name.startswith(("/", "..")) or "/../" in name)


def build(tool: dict, ws: dict, source_path: str | None, seq: int | None) -> pathlib.Path:
    """Write the archive to a temporary file and return its path; the caller deletes it after sending."""
    tmp = tempfile.NamedTemporaryFile(prefix=f"bh-export-{tool['slug']}-", suffix=".tar.gz", delete=False)
    now = int(time.time())
    with tarfile.open(fileobj=tmp, mode="w:gz") as tf:
        def add(name: str, blob: bytes):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime = len(blob), now
            tf.addfile(ti, io.BytesIO(blob))
        if source_path and pathlib.Path(source_path).exists():
            with tarfile.open(source_path, "r:gz") as src:
                for m in src.getmembers():
                    if not _safe(m.name):
                        continue
                    f = src.extractfile(m) if m.isfile() else None
                    m.name = "source/" + re.sub(r"^(\./)+", "", m.name)     # strip only a leading ./, never the dot of .dockerignore
                    tf.addfile(m, f)
        add("database.sql", dump_database(tool))
        blob = data_archive(tool)
        if blob:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as dt:
                for m in dt.getmembers():
                    if not _safe(m.name):
                        continue
                    f = dt.extractfile(m) if m.isfile() else None
                    inner = m.name[5:] if m.name.startswith("data/") else m.name
                    m.name = "data/" + inner if inner and inner != "data" else "data"
                    tf.addfile(m, f)
        add("README.txt", _readme(tool, ws, seq).encode())
    tmp.close()
    return pathlib.Path(tmp.name)
