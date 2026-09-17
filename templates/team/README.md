# Your team notebook

A runnable Boat House starter with team login, Viewer/Editor/Admin roles and persistent notes. It uses Python's standard library and SQLite in `DATA_DIR`; no package installation is needed. Boat House also supplies `DATABASE_URL` if you later switch to PostgreSQL.

## Preview

```sh
python3 app.py --local
```

Open http://127.0.0.1:8080. Preview uses a synthetic admin, binds only to loopback, and saves notes in `.local-data/`. Never use preview mode for public hosting.

## Publish

Connect your agent using **Copy connection** at https://boathousecloud.com/account and add hosting credit. Then your agent runs:

```sh
bh whoami
bh deploy
```

The app is private by default. Share with a real teammate after confirming their email and role:

```sh
bh share YOUR_APP_SLUG teammate@example.com --tier editor
```

Replace the two placeholders. Viewers can read; editors can add notes; admins can change code and sharing. All invited readers see the same notebook. Add record-level authorization before storing data that only some teammates should see.

Update by editing `app.py` and running `bh deploy` again. Notes persist in the mounted data folder. This starter verifies signed gateway identity, rejects stale identities and checks form tokens and write permissions. These checks do not make an arbitrary modified app secure; review your changes and https://boathousecloud.com/security before storing sensitive data.
