"""Operator commands run inside the control container:
  python -m app.bootstrap key <owner-email> [name]   mint a project key for an owner
  python -m app.bootstrap owner <email>              add/promote an owner
  python -m app.bootstrap invite <email>             print an invite link (set/reset password)
  python -m app.bootstrap adopt                      disaster recovery: re-register every running tool
                                                     container from what the docker socket reports
"""
import sys
import time

from . import auth, config, db, deploy, hosts

# Env a tool container carries that Boathouse itself put there; everything else is a secret to keep.
INJECTED = {"PORT", "DATA_DIR", "DATABASE_URL", "TZ", "PATH", "LANG", "HOME", "HOSTNAME"}


def main(argv):
    db.init()
    ws = db.workspace()
    if len(argv) >= 2 and argv[0] == "key":
        email = argv[1].lower()
        with db.conn() as c:
            u = c.execute("SELECT role FROM users WHERE workspace_id=? AND email=?", (ws["id"], email)).fetchone()
        if not u or u["role"] != "owner":
            sys.exit(f"{email} is not an owner; add them first")
        print(auth.create_project_key(ws["id"], email, argv[2] if len(argv) > 2 else "bootstrap"))
        db.audit("bootstrap", "key.create", email)
    elif len(argv) >= 2 and argv[0] == "owner":
        email = argv[1].lower()
        with db.conn() as c:
            c.execute("""INSERT INTO users (id, workspace_id, email, name, role, created, created_by) VALUES (?,?,?,?,?,?,?)
                         ON CONFLICT(workspace_id,email) DO UPDATE SET role='owner'""",
                      (db.new_id("u"), ws["id"], email, None, "owner", time.time(), "bootstrap"))
        db.audit("bootstrap", "user.set", email, {"role": "owner"})
        print(f"{email} is an owner")
    elif len(argv) >= 2 and argv[0] == "invite":
        email = argv[1].lower()
        with db.conn() as c:
            if not c.execute("SELECT 1 FROM users WHERE workspace_id=? AND email=?", (ws["id"], email)).fetchone():
                sys.exit(f"{email} is not in the workspace")
        token = auth.create_invite(ws["id"], email, "bootstrap")
        print(f"https://auth.{hosts.base_for(ws)}/join/{token}")
    elif argv and argv[0] == "adopt":
        adopt(ws)
    else:
        sys.exit(__doc__)


def adopt(ws):
    """Rebuild tool rows from what is actually running. Idempotent: registered tools are left alone."""
    c = deploy.client()
    now = time.time()
    for ctr in c.containers.list(filters={"label": "boathouse.tool"}):
        slug = ctr.labels["boathouse.tool"]
        env = dict(e.split("=", 1) for e in ctr.attrs["Config"]["Env"] if "=" in e)
        if env.get("BOATHOUSE_WORKSPACE") != ws["slug"]:
            print(f"{slug}: belongs to another or an unknown workspace; skipped")
            continue
        resource = ctr.labels.get("boathouse.resource") or slug
        descriptor = {"resource_key": resource}
        if ctr.name != deploy.ctr_name(descriptor):
            print(f"{slug}: container resource identity does not match; skipped")
            continue
        with db.conn() as conn:
            if conn.execute("SELECT 1 FROM tools WHERE workspace_id=? AND slug=?", (ws["id"], slug)).fetchone():
                print(f"{slug}: already registered")
                continue
            signing_key = env.get("BOATHOUSE_SIGNING_KEY")
            url = env.get("DATABASE_URL", "")
            db_password = url.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1] if "://" in url and "@" in url else None
            if not signing_key or not db_password:
                print(f"{slug}: container lacks BOATHOUSE_SIGNING_KEY or DATABASE_URL; skipped")
                continue
            if conn.execute("SELECT 1 FROM tools WHERE resource_key=?", (resource,)).fetchone() or conn.execute(
                    "SELECT 1 FROM tool_keepsakes WHERE resource_key=?", (resource,)).fetchone():
                print(f"{slug}: resource identity already belongs to a registered or retained tool; skipped")
                continue
            tid = ctr.labels.get("boathouse.tool-id") or db.new_id("t")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""INSERT INTO tools (id, workspace_id, slug, name, default_access, default_role, current_release_id,
                            signing_key, db_password, created, created_by, resource_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (tid, ws["id"], slug, slug, "members", "member", None, signing_key, db_password, now, "adopt", resource))
            image = ctr.attrs["Config"]["Image"]
            seqs = sorted({int(t.split(":")[-1]) for img in c.images.list(name=f"bh/{resource}") for t in img.tags if t.startswith(f"bh/{resource}:") and t.split(":")[-1].isdigit()})
            live = int(image.split(":")[-1]) if image.split(":")[-1].isdigit() else (seqs[-1] if seqs else 1)
            cur = None
            for seq in seqs or [live]:
                rid = db.new_id("r")
                source = config.SOURCE_DIR / tid / f"{seq}.tar.gz"
                if not source.exists() and resource == slug:
                    source = config.SOURCE_DIR / slug / f"{seq}.tar.gz"
                conn.execute("""INSERT INTO releases (id, tool_id, seq, image, status, note, log, created, created_by, source)
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                             (rid, tid, seq, image if seq == live else deploy.image_tag(descriptor, seq), "live" if seq == live else "superseded",
                              "adopted from the running box", None, now, "adopt", str(source) if source.exists() else None))
                if seq == live:
                    cur = rid
            conn.execute("UPDATE tools SET current_release_id=? WHERE id=?", (cur, tid))
            extras = [k for k in env if k not in INJECTED and not k.startswith(("BOATHOUSE_", "PYTHON", "GPG_KEY"))]
            for k in extras:
                conn.execute("INSERT INTO secrets VALUES (?,?,?,?,?)", (tid, k, auth.encrypt(env[k]), now, "adopt"))
            conn.execute("COMMIT")
            print(f"{slug}: adopted (image {image}, releases {seqs or [live]}, secrets {extras})")
    db.audit("adopt", "state.adopted", None, None)


if __name__ == "__main__":
    main(sys.argv[1:])
