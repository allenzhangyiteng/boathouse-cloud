# How do I create an app that is ready for Boat House?

Run `bh init`. It creates a complete project locally: app files, `boathouse.json`, instructions for your coding agent, and a README with preview and deployment commands. It needs no login, makes no network requests and refuses to overwrite existing files.

## Runnable team-app example

After [installing the CLI](/docs/install-agent):

```sh
bh init team-notebook --template team --name "Team notebook"
cd team-notebook
python3 app.py --local
```

Open http://127.0.0.1:8080 and add a note. Restart the preview: your note is still there. Local preview uses a synthetic admin and binds only to your computer. The sample data folder is excluded from deployment. Choose a fresh folder if `team-notebook` already exists.

The team template uses Python's standard library. On Boat House it verifies the gateway's signed identity, checks the timestamp, gives viewers read-only access and lets editors add notes. Form tokens protect writes. Notes are stored in SQLite inside the persistent `DATA_DIR`. Everyone with access to this notebook can read the same notes; add record-level authorization if your application needs more private data within a team.

## A website instead

```sh
bh init company-site --template static
cd company-site
python3 -m http.server 8080 --bind 127.0.0.1
```

Edit `index.html`. [Publish the website](/docs/publish-static-website) when ready.

## The app format

`boathouse.json` starts with `schemaVersion`, `slug` and `name`. The CLI adds deployment metadata such as `base` and `workspace` after publishing; keep those fields so stale updates and workspace mistakes are caught. The slug selects the app, so changing it creates a different deployment target.

A static app has an `index.html` at the project root. A server app has a `Dockerfile`, listens on `0.0.0.0:$PORT` (8080 by default), and writes persistent files only under `DATA_DIR`. Boat House provides `DATABASE_URL` for PostgreSQL and signed identity headers plus `BOATHOUSE_SIGNING_KEY` for authorization. Keep secrets in environment variables, never in client code or source archives.

There is no proprietary runtime dependency. You own ordinary Python/HTML/Docker source and can export your app and data. The templates are Apache 2.0 licensed.

## Publish after preview

Stop the local server, connect your account and add hosting credit. From the app folder:

```sh
bh whoami
bh deploy
```

New apps are private by default. [Share access](/docs/share-with-team), then verify with a second account before using real business data. Read the [security limitations](/security) first.
