# How do I deploy a Claude Code app with team login?

Connect Claude Code to Boat House once, then say “Put this app online and keep it private to my team.” Your agent prepares the files, runs the deployment and returns a stable HTTPS link. Codex, Cursor and Windsurf can use the same CLI and app format.

You do not need separate hosting, database and sign-in projects. Boat House supplies those pieces. Your app still needs to implement its own data permissions and any connections to outside services.

## Runnable example

After [installing Boat House](/docs/install-agent), start with a complete working app:

```sh
bh init launch-demo --template team
cd launch-demo
python3 app.py --local
```

Open http://127.0.0.1:8080, add a note, then stop the preview with Ctrl+C. This runs without an account or payment.

To publish, sign up at Boat House, confirm your email and paste **Copy connection** from [your account](/account) into Claude Code. Add hosting credit in the browser. Then the agent runs from the project folder:

```sh
bh whoami
bh deploy
bh status launch-demo
```

Use the URL printed by `bh deploy`; it contains your actual organization address. Open it while signed out to check that login is required, then sign in. Add a note and reload. The local preview's synthetic notes were not uploaded; the hosted app starts with its own persistent data.

## Deploy the app you already built

The agent checks for an `index.html` or a `Dockerfile` and adapts the project if necessary. A server must listen on the supplied `PORT`, store files in `DATA_DIR`, and use `DATABASE_URL` when it needs the managed PostgreSQL database. Native serverless/edge functions may need adaptation; an existing database needs a planned data migration. Existing source is not overwritten by `bh init`.

Ask the agent to check logs and exercise the real workflow after deployment. Publishing a working container does not prove every feature or permission works.

## Share and update

Tell your agent the teammate's email and whether they should view, edit data or administer the app. [Sharing instructions](/docs/share-with-team) explain those roles. To publish later changes, run `bh deploy` again from the same folder; Boat House keeps releases and rejects stale source updates.

Managed hosting costs $10 per organization per month for up to five lightweight tools. Connecting an agent can take about 90 seconds after email confirmation; payment, dependency installation and builds take additional time. Domain purchases and approved storage increases are separate.
