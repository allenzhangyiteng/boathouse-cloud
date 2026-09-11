"""Docker orchestration: build, run, roll back, log. One container per tool,
one private network per tool (tool + caddy + postgres only), one Postgres
database per tool, hard resource limits. The control plane never joins a tool
network except for the few seconds of a health probe."""
import io
import os
import fcntl
import re
from contextlib import contextmanager
import json
import socket
import uuid
import shutil
from pathlib import Path
import tarfile
import time

import docker
import psycopg
from psycopg import sql
from docker.errors import NotFound, APIError

from . import auth, config, db, hosts, sandbox

CADDY = os.environ.get("BH_CADDY_CONTAINER", "bh-caddy")
POSTGRES = os.environ.get("BH_POSTGRES_CONTAINER", "bh-postgres")
CONTROL = os.environ.get("BH_CONTROL_CONTAINER", "bh-control")


def client():
    return docker.from_env()


def resource_key(tool):
    """Internal resource identity; a bare user-chosen slug is never sufficient."""
    if isinstance(tool, str):
        raise TypeError("a tool record with resource_key is required, not a slug")
    key = tool["resource_key"]
    if not key or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", key):
        raise ValueError("tool has no valid persisted resource identity")
    return key


def net_name(tool): return f"bh-net-{resource_key(tool)}"
def ctr_name(tool): return f"tool-{resource_key(tool)}"
def vol_name(tool): return f"bh-{resource_key(tool)}-{'bounded-' if config.RESOURCE_GUARD else ''}data"
def image_tag(tool, seq): return f"bh/{resource_key(tool)}:{seq}"
def db_name(tool): return "t_" + resource_key(tool).replace("-", "_")


def resource_labels(tool):
    return {"boathouse.tool": tool["slug"], "boathouse.workspace": tool["workspace_id"],
            "boathouse.resource": resource_key(tool), "boathouse.tool-id": tool["id"]}


@contextmanager
def operation(tool):
    """Serialize mutations across worker processes as well as async requests."""
    folder = config.STATE_DIR / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{resource_key(tool)}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


# ---- infrastructure per tool --------------------------------------------------

def ensure_network(tool):
    c = client()
    try:
        net = c.networks.get(net_name(tool))
    except NotFound:
        net = c.networks.create(net_name(tool), driver="bridge",
                                labels=resource_labels(tool))
    for member in (CADDY, POSTGRES):
        try:
            ctr = c.containers.get(member)
        except NotFound:
            continue
        nets = ctr.attrs["NetworkSettings"]["Networks"]
        if net_name(tool) not in nets:
            # postgres answers to "postgres" on every tool network, as it does on bh-core
            net.connect(ctr, aliases=[config.PG_HOST] if member == POSTGRES else None)
    return net


def ensure_volume(tool):
    c = client()
    try:
        return c.volumes.get(vol_name(tool))
    except NotFound:
        if config.RESOURCE_GUARD:
            from . import resources
            allocation=resources.ensure(tool)
            return c.volumes.create(vol_name(tool),labels=resource_labels(tool),driver='local',
                                    driver_opts={'type':'none','o':'bind','device':allocation['files_path']})
        return c.volumes.create(vol_name(tool), labels=resource_labels(tool))


def ensure_database(tool, password: str):
    name = db_name(tool)
    space = 'q_'+resource_key(tool).replace('-','_')
    allocation = None
    if config.RESOURCE_GUARD:
        from . import resources
        allocation = resources.ensure(tool)
    with psycopg.connect(host=config.PG_HOST, user="postgres", password=config.PG_ADMIN_PASSWORD,
                         dbname="postgres", autocommit=True) as pg:
        exists = pg.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
        if not exists:
            pg.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(name), sql.Literal(password)))
        else:
            pg.execute(sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(sql.Identifier(name), sql.Literal(password)))
        if allocation:
            if not pg.execute('SELECT 1 FROM pg_tablespace WHERE spcname=%s',(space,)).fetchone():
                pg.execute(sql.SQL('CREATE TABLESPACE {} LOCATION {}').format(sql.Identifier(space),sql.Literal(allocation['postgres_path'])))
            pg.execute(sql.SQL('GRANT CREATE ON TABLESPACE {} TO {}').format(sql.Identifier(space),sql.Identifier(name)))
            pg.execute('REVOKE CREATE ON TABLESPACE pg_default FROM PUBLIC')
            pg.execute(sql.SQL('ALTER ROLE {} CONNECTION LIMIT {}').format(sql.Identifier(name),sql.Literal(config.PG_CONNECTION_LIMIT)))
            for setting,value in [('statement_timeout',str(config.PG_QUERY_SECONDS*1000)),('idle_in_transaction_session_timeout','30000'),('temp_file_limit','65536')]:
                pg.execute(sql.SQL('ALTER ROLE {} SET {} = {}').format(sql.Identifier(name),sql.Identifier(setting),sql.Literal(value)))
        existing=pg.execute('SELECT t.spcname FROM pg_database d JOIN pg_tablespace t ON t.oid=d.dattablespace WHERE d.datname=%s',(name,)).fetchone()
        if not existing:
            suffix=sql.SQL(' TABLESPACE {}').format(sql.Identifier(space)) if allocation else sql.SQL('')
            pg.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(name))+suffix)
        elif allocation and existing[0]!=space:
            raise RuntimeError('This database needs the operator’s quota migration before the app can restart. Existing data has not been moved.')
        # PostgreSQL grants CONNECT and TEMP to PUBLIC by default. Each tool
        # must enter only its own database, even when it knows a sibling's name.
        pg.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        pg.execute(sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(
            sql.Identifier(name), sql.Identifier(name)))
    return f"postgresql://{name}:{password}@{config.PG_HOST}:5432/{name}"


def tool_env(tool, ws) -> dict:
    base = hosts.base_for(ws)
    env = {
        "PORT": str(config.TOOL_PORT),
        "DATA_DIR": "/data",
        "DATABASE_URL": ensure_database(tool, tool["db_password"]),
        "BOATHOUSE_TOOL": tool["slug"],
        "BOATHOUSE_URL": f"https://{tool['slug']}.{base}",
        "BOATHOUSE_AUTH_URL": f"https://auth.{base}",
        "BOATHOUSE_WORKSPACE": ws["slug"],
        "BOATHOUSE_SIGNING_KEY": tool["signing_key"],
        "TZ": "America/New_York",
    }
    with db.conn() as c:
        for row in c.execute("SELECT name, value_enc FROM secrets WHERE tool_id=?", (tool["id"],)):
            env[row["name"]] = auth.decrypt(row["value_enc"])
    return env


# ---- build ----------------------------------------------------------------------

STATIC_DOCKERFILE = """FROM nginx:1.27-alpine
COPY . /usr/share/nginx/html
RUN printf 'server { listen 8080; root /usr/share/nginx/html; index index.html; location / { try_files $uri $uri/ $uri.html /index.html; } }' > /etc/nginx/conf.d/default.conf
"""


def ensure_dockerfile(context: bytes) -> bytes:
    """A folder with a Dockerfile builds as it is. A folder that is just a website (an index.html at the top)
    gets a small nginx Dockerfile added, so a static site deploys with no setup at all."""
    with tarfile.open(fileobj=io.BytesIO(context)) as tf:
        names = [m.name.lstrip("./") for m in tf.getmembers()]
    if "Dockerfile" in names:
        return context
    if "index.html" not in names:
        raise ValueError("no Dockerfile and no index.html at the top of the uploaded folder: a tool is either a folder with a "
                         "Dockerfile that listens on $PORT (8080), or a website folder with an index.html")
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as dst, tarfile.open(fileobj=io.BytesIO(context)) as src:
        for m in src.getmembers():
            dst.addfile(m, src.extractfile(m) if m.isfile() else None)
        body = STATIC_DOCKERFILE.encode()
        ti = tarfile.TarInfo("Dockerfile")
        ti.size, ti.mtime = len(body), int(time.time())
        dst.addfile(ti, io.BytesIO(body))
    return out.getvalue()

def build(tool, seq: int, context: bytes) -> tuple[str, str]:
    """Build the image from an uploaded tar of the tool folder. Returns (tag, log)."""
    tag = image_tag(tool, seq)
    context = ensure_dockerfile(context)
    if config.RESOURCE_GUARD:
        from . import bounded_build
        return bounded_build.build(tool,seq,context)
    api = client().api
    lines = []
    for chunk in api.build(fileobj=io.BytesIO(context), custom_context=True, tag=tag, rm=True,
                           forcerm=True, decode=True, pull=False):
        if "stream" in chunk:
            lines.append(chunk["stream"].rstrip())
        elif "error" in chunk:
            lines.append("ERROR: " + chunk["error"])
            raise RuntimeError("\n".join(lines[-40:]))
    return tag, "\n".join(lines)


# ---- run / stop -----------------------------------------------------------------

def _probe(tool, timeout: float = 60.0) -> bool:
    """Join the tool network briefly, wait for the port to open, leave."""
    c = client()
    net = c.networks.get(net_name(tool))
    me = c.containers.get(CONTROL)
    joined = False
    try:
        net.connect(me)
        joined = True
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection((ctr_name(tool), config.TOOL_PORT), timeout=2):
                    return True
            except OSError:
                ctr = c.containers.get(ctr_name(tool))
                if ctr.status == "exited":
                    return False
                time.sleep(1)
        return False
    finally:
        if joined:
            try:
                net.disconnect(me)
            except APIError:
                pass


def run(tool, tag: str, ws) -> str:
    from . import resources
    with resources.host_operation():
        resources.admission(tool)
        return _run(tool,tag,ws)

def _run(tool, tag: str, ws) -> str:
    """Start the tool on this image, replacing whatever is running. Returns container id.
    On a failed health probe the old container is left stopped, not removed, and
    the failure log is raised so the release record carries it."""
    c = client()
    ensure_network(tool)
    ensure_volume(tool)
    env = tool_env(tool, ws)
    try:                                   # before touching the running tool: is there anything to start?
        c.images.get(tag)
    except NotFound:
        raise RuntimeError(f"the image for this release ({tag}) is no longer on the box, so it cannot be started. "
                           "Nothing was changed; pick a release that `releases` lists as live or superseded.")
    old = None
    previous_name = f"{ctr_name(tool)}-prev"
    # A failed cleanup from a previous successful deployment must not strand the
    # current container under a conflicting name after it has been stopped.
    try:
        c.containers.get(previous_name).remove(force=True)
    except NotFound:
        pass
    try:
        old = c.containers.get(ctr_name(tool))
    except NotFound:
        pass
    if old is not None:
        old.stop(timeout=10)
        try:
            old.rename(previous_name)
        except Exception:
            old.start()
            raise
    ctr = None
    try:
        ctr = _start(c, tag, tool, env)
        if not _probe(tool):
            log = ctr.logs(tail=60).decode(errors="replace")
            raise RuntimeError(f"tool did not open port {config.TOOL_PORT} within 60s\n--- last log lines ---\n{log}")
    except Exception as error:
        # Docker may create the container and then fail to start it; remove that
        # partial replacement before giving the original its name back.
        try:
            replacement = ctr if ctr is not None else c.containers.get(ctr_name(tool))
            replacement.remove(force=True)
        except NotFound:
            pass
        if old is not None:
            old.rename(ctr_name(tool))
            old.start()
        outcome = "the previous release was restarted" if old is not None else "nothing is running yet"
        raise RuntimeError(f"{error}\n{outcome}") from error
    if old is not None:
        try:
            old.remove(force=True)
        except APIError as error:
            db.audit("deploy", "container.cleanup_failed", tool["slug"], {"error": str(error)[:200]}, ws["id"])
    prune_runtime(tool)
    return ctr.id


def _start(c, tag: str, tool, env: dict):
    if config.RESOURCE_GUARD:
        declared=(c.images.get(tag).attrs.get('Config') or {}).get('Volumes') or {}
        allowed={'/data','/etc/nginx/conf.d','/tmp','/run','/var/cache/nginx','/root/.gunicorn'}
        if set(declared)-allowed:
            raise ValueError('The app image declares an unbounded volume. Remove its VOLUME instruction and store persistent files in DATA_DIR (/data).')
    volumes={vol_name(tool): {'bind':'/data','mode':'rw'}}
    if config.RESOURCE_GUARD:
        from . import resources
        allocation=resources.ensure(tool)
        runtime=Path(allocation['files_path']).parent/'runtime'/uuid.uuid4().hex
        runtime.mkdir(parents=True,mode=0o755)
        name='bh-'+resource_key(tool)+'-runtime-'+runtime.name
        volume=c.volumes.create(name,driver='local',driver_opts={'type':'none','o':'bind','device':str(runtime)},
                                labels={'boathouse.runtime':resource_key(tool)})
        # Copy image defaults into an empty named volume, then allow bounded
        # Nginx configuration rendering without making the app root writable.
        volumes[volume.name]={'bind':'/etc/nginx/conf.d','mode':'rw'}
    return c.containers.run(
        tag, name=ctr_name(tool), detach=True, environment=env,
        network=net_name(tool), volumes=volumes,
        mem_limit=config.TOOL_MEMORY, nano_cpus=int(config.TOOL_CPUS * 1e9), pids_limit=config.TOOL_PIDS, cpu_shares=128,
        memswap_limit=config.TOOL_MEMORY,
        read_only=config.TOOL_READONLY,
        tmpfs=({'/tmp':'rw,nosuid,size=64m','/run':'rw,nosuid,size=16m','/var/cache/nginx':'rw,nosuid,size=32m','/root/.gunicorn':'rw,nosuid,size=4m'} if config.TOOL_READONLY else None),
        restart_policy={"Name": "unless-stopped"},
        log_config={"type": "json-file", "config": {"max-size": "10m", "max-file": "3"}},
        labels={**resource_labels(tool), "boathouse.image": tag},
        security_opt=["no-new-privileges:true"] + (["seccomp=" + sandbox.profile()] if config.RESOURCE_GUARD else []),
    )


def prune_runtime(tool):
    if not config.RESOURCE_GUARD:return
    from . import resources
    allocation=resources.measure(tool)
    parent=Path(allocation['files_path']).parent/'runtime'
    for volume in client().volumes.list(filters={'label':'boathouse.runtime='+resource_key(tool)}):
        volume.reload()
        path=Path((volume.attrs.get('Options') or {}).get('device','/invalid'))
        if path.parent!=parent or not re.fullmatch('[a-f0-9]{32}',path.name) or path.is_symlink():continue
        try:volume.remove(force=False)
        except APIError:continue
        if path.exists():shutil.rmtree(path)


def restart(tool, ws):
    with operation(tool):
        with db.conn() as connection:
            tool = connection.execute("SELECT * FROM tools WHERE id=?", (tool["id"],)).fetchone()
        if tool is None:
            raise ValueError("tool was deleted while this operation was waiting")
        return _restart(tool, ws)


def _restart(tool, ws):
    c = client()
    ctr = c.containers.get(ctr_name(tool))
    tag = ctr.attrs["Config"]["Image"]
    return run(tool, tag, ws)


def stop(tool):
    try:
        client().containers.get(ctr_name(tool)).stop(timeout=10)
    except NotFound:
        pass


def remove(tool, purge: bool):
    c = client()
    for name in (ctr_name(tool), f"{ctr_name(tool)}-prev"):
        try:
            c.containers.get(name).remove(force=True)
        except NotFound:
            pass
    try:
        net = c.networks.get(net_name(tool))
        for member in (CADDY, POSTGRES, CONTROL):
            try:
                net.disconnect(c.containers.get(member))
            except (NotFound, APIError):
                pass
        net.remove()
    except NotFound:
        pass
    for tag in _image_tags(c, tool):
        try:
            # Identical builds can share an image ID across tenants. Remove only
            # our tag, never that shared image ID and its other tenants' tags.
            c.images.remove(tag, force=True)
        except APIError:
            pass
    prune_runtime(tool)
    if purge:
        try:
            c.volumes.get(vol_name(tool)).remove(force=True)
        except NotFound:
            pass
        with psycopg.connect(host=config.PG_HOST, user="postgres", password=config.PG_ADMIN_PASSWORD,
                             dbname="postgres", autocommit=True) as pg:
            pg.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name(tool))))
            if config.RESOURCE_GUARD:
                pg.execute(sql.SQL("DROP TABLESPACE IF EXISTS {}").format(sql.Identifier("q_"+resource_key(tool).replace("-","_"))))
            pg.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(db_name(tool))))

        if config.RESOURCE_GUARD:
            from . import resources
            resources.broker('DELETE','/resources/'+resource_key(tool),{'purge':True})
            with db.conn() as connection:
                connection.execute('DELETE FROM resource_limits WHERE resource_key=?',(resource_key(tool),))


def collapse_repeats(text: str) -> str:
    """A crash-looping container prints the same lines once per restart (a traceback, or a bare SyntaxError with no
    header). Say the block once and count the rest. Only the repeating tail is folded; lines before it stay."""
    lines = text.rstrip("\n").split("\n")
    n = len(lines)
    for p in range(1, n // 2 + 1):
        k = 0
        while k < n and lines[n - 1 - k] == lines[n - 1 - (k % p)]:
            k += 1
        if k >= max(2 * p, 4) and any(l.strip() for l in lines[n - p:]):
            times = -(-k // p)
            return "\n".join(lines[:n - k] + lines[n - p:] +
                             [f"(this repeated {times} times: the tool crashed and restarted {times} times)"])
    return text


def cause_line(log: str) -> str:
    """The one line of a failed build or start that says what went wrong: the last one naming an error, else the last real line."""
    ls = [l.strip() for l in (log or "").strip().splitlines() if l.strip() and l.strip() not in ("^", "~")
          and not l.strip().startswith(("(this repeated", "(the tool crashed"))]
    for l in reversed(ls):
        if any(w in l.lower() for w in ("error", "exception", "failed", "cannot", "not found", "refused", "denied", "no such")):
            return l[:200]
    return (ls[-1] if ls else "")[:200]


def prune_images(tool, keep_tags: set[str]):
    c = client()
    for tag in _image_tags(c, tool):
        if tag not in keep_tags:
            try:
                c.images.remove(tag, force=True)
            except APIError:
                pass


def _image_tags(c, tool):
    repo = f"bh/{resource_key(tool)}"
    return {tag for img in c.images.list(name=repo) for tag in img.tags if tag.rsplit(":", 1)[0] == repo}


# ---- observe ----------------------------------------------------------------------

def status(tool) -> dict:
    try:
        ctr = client().containers.get(ctr_name(tool))
    except NotFound:
        return {"state": "absent"}
    except Exception as e:  # noqa: BLE001  (docker unreachable: say so, do not take the page down)
        return {"state": "unknown", "error": str(e)[:120]}
    st = ctr.attrs["State"]
    return {
        "state": ctr.status,
        "started": st.get("StartedAt"),
        "restarts": ctr.attrs.get("RestartCount", 0),
        "image": ctr.attrs["Config"]["Image"],
        "exit_code": st.get("ExitCode") if ctr.status == "exited" else None,
    }


def logs(tool, tail: int = 200, since: int | None = None) -> str:
    try:
        ctr = client().containers.get(ctr_name(tool))
    except NotFound:
        return ""
    except Exception as e:  # noqa: BLE001
        return f"(logs unavailable: {str(e)[:120]})\n"
    kw = {"tail": tail, "timestamps": True}
    if since:
        kw["since"] = since
    return ctr.logs(**kw).decode(errors="replace")
