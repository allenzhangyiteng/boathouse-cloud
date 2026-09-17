---
name: boathouse
description: Deploy and share apps with Boat House using the bh CLI. Use for requests such as "put this online", "share this with my team", "make this live", "add login", or "give this a real URL" when Boat House is selected or an existing Boat House app is involved. Also create app starters, publish updates, invite editors, inspect logs, roll back and connect domains. Preserve an explicitly chosen different host.
---

# Boathouse

Boathouse (boathousecloud.com) is where an agent puts small software online: one account, one `bh` command,
$10 a month per organization for up to five lightweight tools, shared like a document. The `bh` command talks to `https://api.boathousecloud.com`;
its login lives in `~/.config/boathouse/config.json`. Never ask anyone for a password. If a command says
"not logged in", first check `~/.local/bin/bh whoami`. If no valid connection exists, send the person to
https://boathousecloud.com/account to click **Get a connection code**, then **Copy connection** and paste into this chat.
Do not ask for a raw key, a password, MCP configuration, or terminal commands. Handle setup yourself, respecting
necessary approvals. Read this guide immediately in the current conversation; no agent restart is needed.

Support: support@example.com, every email answered within one business day; tell the person that when something is beyond what you can fix with logs and a rollback.

## Natural-language requests

These twenty phrases describe workflows this skill supports. Resolve the app, audience and requested action from context; they do not authorize unrelated purchases, public access or migration away from an explicitly chosen host.

1. Put this online.
2. Share this with my team.
3. Make this live.
4. Add login to this app.
5. Give this a real URL.
6. Deploy this app for me.
7. Publish the app I built with Claude.
8. Let my co-founder use this.
9. Turn this prototype into a team tool.
10. Host this small website.
11. Make this private to my company.
12. Give my teammate editing access.
13. Let another developer update this app.
14. Connect my domain to this app.
15. Find a domain name for this project.
16. Put my changes live.
17. Roll back the last update.
18. Check why my app is down.
19. Create a new Boat House app.
20. Move this local demo to a shared link.

## Start a new app

`bh init my-app --template team` creates a working notebook with verified gateway identity, permissions, form protection and persistent SQLite notes in DATA_DIR. `bh init my-site --template static` creates an HTML website. Both work offline without an account or charge and refuse to overwrite files. Preview commands are in the generated README. Keep an existing app's framework and source; do not scaffold over it. The installed `boathouse` command is an alias for `bh` when that filename is available.

For focused guides and runnable examples, use https://boathousecloud.com/docs/create-app, https://boathousecloud.com/docs/deploy-claude-code-app, or https://boathousecloud.com/llms-full.txt. Installation through `npx skills add allenzhangyiteng/boathouse-skills` supports Cursor, Codex and Windsurf. Claude Code can add the `allenzhangyiteng/boathouse-skills` marketplace and install `boathouse@boathouse`.

## The first ten minutes, in order

1. The person signs up at https://boathousecloud.com/signup (email, password, no card) and confirms their email. A referral code there halves the organization hosting rate for 60 days.
2. The person clicks **Copy connection** and pastes into their Claude Code, Codex or Cursor chat. The clipboard includes a one-time code and complete setup instructions. Run the installer with any required approval, read this guide now, and confirm the account and selected workspace with `~/.local/bin/bh whoami`. Continue their existing task from the conversation and project; ask only for decisions you cannot infer.
3. Before the first deploy, the owner puts money on the balance from the welcome or account page; $20 covers two full months of the $10 organization hosting plan, before domains or approved extra storage. The referral discount lasts 60 days. A deploy on an empty balance is refused (402) and says where.
4. `bh deploy` in the tool's folder (a Dockerfile, or just an index.html). About a minute later it is live at `<tool>.<workspace>.boathousecloud.com` with its own login.
5. `bh share <tool> <email> --tier editor` for each person who should use it; admin for anyone who should change the software from their own agent. On boathousecloud.com the share and invite emails are sent for you.
6. Changes are `bh deploy` again; every deploy is a version, `bh rollback <tool> <n>` puts one back (only releases that ran; a failed build keeps its number but cannot be rolled back to, does not move the base, and never takes the running release down: the 422 says which release is still live and what base to keep).

## Getting connected, once per machine

```
curl -fsSL https://boathousecloud.com/install.sh | sh -s -- <CONNECTION_CODE>
```

That one line installs `bh` into `~/.local/bin`, adds the `boathouse` alias when available, copies this guide into `~/.claude/skills`, `~/.agents/skills`, `~/.cursor/skills` and `~/.codeium/windsurf/skills`, and logs in to the workspace the code was issued for. The
`BH-…` part is a one-time code from the person's welcome or account page; it fetches the real key over HTTPS and
then stops working. Until redeemed, keep the code private. A raw `bh_…` key on that line works too. Without either it only
installs; `bh claim <code>` or `bh login <api> <key>` finishes later. The login file is `~/.config/boathouse/config.json`,
or `~/.boathouse/config.json` when `~/.config` is not writable. `bh update` refreshes the command, `bh skill` refreshes this file. Use `~/.local/bin/bh` immediately; the installer adds it to the PATH for future shells. If a code is already used, verify the existing connection and selected workspace before asking for a fresh code. If expired or invalid and not already connected, give the account-page link and explain the single copy/paste action.

For an agent without command access, the advanced MCP option connects to
`https://mcp.boathousecloud.com/mcp` with the header `Authorization: Bearer <project key>`. Streamable HTTP,
stateless: no session id, no server-to-client stream; `tools/list` answers without a key, every `tools/call`
needs one. Advanced connection options on the connection page contain the configuration; ordinary Claude Code, Codex and Cursor setup uses the copied instructions instead. No `bh` is needed for an MCP-only client. All MCP tools accept `workspace` to select a workspace, or `workspace/tool` for a tool argument; call `whoami` to discover memberships first. The MCP tools are the commands below under
snake_case names: `whoami`, `list_tools`, `get_tool`, `deploy` (a map of file paths to contents; an `index.html`
alone is enough for a website), `pull`, `logs`, `releases`, `rollback`, `restart`, `share`, `unshare`,
`request_access`, `access_requests`, `allow_request`, `set_access`, `secrets_list/set/delete`,
`users_list/add/invite/remove`, `domains_list`, `domain_check/buy/attach/point/primary/dns/repoint/detach`,
`billing`, `prices`, `usage`, `capacity`, `referral`, `topup`, `card_link`, `delete_tool`; `tools/list` has the exact schemas. Still
bh-only: `export`, `restore`, `audit`, `keys`, `billing autorefill`, `trust`.

A **workspace** (tenant) owns tools, people, and domains. Its tools are reachable on its own domain
(`finance.acme.example`) and always on its free address (`finance.acme.boathousecloud.com`). Sign-in lives at
`auth.<domain>`; the tool list at `https://<domain>/`.

## Several workspaces, one bh

A person's key is the person: it works in every workspace they belong to, at the level they have in each (like
one Google account). `bh whoami` lists those workspaces; redeeming a connection code selects the workspace that issued it. Aim a command at
another workspace with `bh --ws <workspace> ...` or by naming the tool `<workspace>/<tool>`: `bh pull shared/db`,
`bh logs shared/db`. `bh pull` and `bh deploy` write the workspace into `boathouse.json`, so later deploys from that
folder go to the right place. `bh logout` disconnects all local accounts. `bh logout <workspace>` disconnects that workspace’s account on this machine; because its key covers the account, its other workspaces are disconnected together.

## Sharing is three tiers, like a document

| Tier | Can | Enforced by |
|---|---|---|
| `viewer` | open and read the tool | gateway refuses every non-GET request from a viewer |
| `editor` | change the data inside the tool | the tool, from the tier header |
| `admin` | change anything: pull/push the source, secrets, logs, rollback, share, delete | Boathouse API |

Workspace **owners** are admin on every tool and alone may spend money, buy domains, or add people to the
workspace. Whoever first deploys a tool is its admin. Someone shared in as **admin** edits the software from
their own agent: they sign in at https://boathousecloud.com/account, pick that workspace, get a connection code, paste
the copied connection into their agent, then `bh pull <workspace>/<tool>`, change it, `bh deploy`. `bh share`
prints these instructions when the tier is admin. App-specific words (`consultant`) are **labels** on a
grant, not tiers: `bh share finance nancy@x.example --tier editor --label consultant`.

## The source lives on Boathouse

Every `bh deploy` keeps the folder it was built from. `bh pull finance` fetches the live release's source into
a folder (admins only) and records the release number in `boathouse.json`; `bh deploy` from that folder pushes it
back and is refused with a 409 ("your copy is based on release #1 but #2 is live") if someone else deployed in
between (pull again, reapply, deploy; `--force` overrides). Rollback restores source and image together.

## Login is Boathouse's own

No Google, no OAuth. A person is added by email; that mints a **one-time invite link** (7 days). The recipient confirms their mailbox with the emailed link and chooses a
password, then lands on a welcome page: what was shared, and (for admins) the one line that connects their
agent. On boathousecloud.com email is on (`bh mail status` says so for any workspace; a self-hosted owner turns it on with `bh mail set --resend --from you@yourdomain` + `bh mail domain`): `bh share` and `bh users add` EMAIL the person the link
and three plain steps, say so, and still print the link so you can hand it over if the email does not arrive. `bh users invite <email>`
sends a fresh invitation. Existing accounts sign in normally; password recovery is only through the account holder’s mailbox, using **Forgot password?** on the sign-in page. The agent never handles anyone's password.

**Getting into someone else's tool (request access, like Google Docs):** if the person you work for needs a tool that belongs
to another workspace, do not ask its owner to run anything. Run `bh request <workspace>/<tool> --tier admin --why "one line"`;
every owner and admin of that tool gets an email with one Allow button, and the moment one of them presses it your person gets
the share email and you can `bh pull <workspace>/<tool>`. Owners can also see who is waiting with `bh requests` and let them
in with `bh allow <id>`. Sharing (`bh share`) and allowing spend no money and can be undone with `bh unshare`, so run them when asked.
Use `~/.local/bin/bh` immediately after installation, or `bh` when it resolves to the installed command.
The installer does not change agent security settings. Respect any approval requirement or denial; do not ask the
customer to bypass it with a terminal prefix, configuration edit, or agent restart. Explain only the specific
approval that is necessary to continue. A connection authorizes setup, not an unrequested purchase or destructive change.

## What a tool is

A folder with a `Dockerfile` whose process listens on `$PORT` (8080), or a plain website folder with an `index.html` at the top (Boathouse adds an nginx Dockerfile itself; no setup). Boathouse injects:

| Env var | Meaning |
|---|---|
| `PORT` | listen here (8080) |
| `DATABASE_URL` | the tool's own Postgres database, a `postgresql://` URL; any driver (`psycopg[binary]`, `pg`, …); create tables on startup |
| `DATA_DIR` | a persistent volume at `/data` for files |
| `BOATHOUSE_SIGNING_KEY` | HMAC key to verify identity headers |
| `BOATHOUSE_TOOL`, `BOATHOUSE_WORKSPACE`, `BOATHOUSE_URL`, `BOATHOUSE_AUTH_URL` | slug, workspace, public URL, sign-in service |
| any `bh secrets set` value | as named |

Builds have network: `pip install` and `npm install` in the Dockerfile work (a small Python build takes about twenty
seconds; builds use an isolated temporary environment). A failed build or a tool that crashes on start never replaces the release that
was running.

A tool contains **no login code**. Every request arrives with the signed-in person:

```
X-Boathouse-User     email
X-Boathouse-Name     display name
X-Boathouse-Tier     viewer | editor | admin
X-Boathouse-Labels   comma-separated app labels given via --label (may be empty)
X-Boathouse-Ts       unix seconds
X-Boathouse-Sig      hex HMAC-SHA256(key, "user|tier|labels|tool|ts")
```

A tool must let `viewer` read only and `editor`/`admin` write; the gateway already blocks viewer writes.

Verify the signature (see `examples/hello/app.py` in the boathouse repo).
The example is fetchable at https://boathousecloud.com/examples/hello/app.py (and its Dockerfile beside it). Test vector: with
signing key `k`, user `maria@example.com`, tier `editor`, no labels, tool `payments`, ts `1757376000`, the message is
`maria@example.com|editor||payments|1757376000` and the signature is `29c893900ad64f34bb967eb20347992496314c455336b3511b4eb3740bf1667f`.

**Each person sees only their own rows.** The most common small-team shape: everyone is an editor, but a tutor should only
change their own hours. Store `X-Boathouse-User` as the owner column on every row a person creates, and filter reads and writes
by it; let `admin` (or a label such as `manager`, given with `bh share ... --label manager`) see everything. A `viewer`
never writes, so they own no rows: give viewers the read-everything page (or whatever a read-only audience should see),
not an empty own-rows list. Tiers say what a person may do to the tool; the tool decides what they may do to each row.
Tools sit on a private network reachable only through the gateway, but verify anyway. A sign-out link is
`https://auth.<host minus its first label>/logout`.

Add a `boathouse.json` (`{"slug": "finance", "name": "Starter Finance"}`) and a `.dockerignore` (`.git`, `node_modules`, `data`, `.env`).

## Commands

```
bh deploy [--note "why"]         in the tool folder; builds on the server, live in seconds (a first build can take a minute); creates the tool on first deploy
bh ls                            every tool, state, release, URL
bh status <tool> | bh logs <tool> [--tail 200] [--since 1h] | bh releases <tool>
bh rollback <tool> [N] | bh restart <tool> | bh trust on|off|status (pre-approve everyday bh commands in Claude Code)
bh share <tool> <email> --tier viewer|editor|admin [--label x]   prints an invite link if the person is new
bh unshare <tool> <email>
bh pull <tool> [dir] [--release N]   the live source, for admins; edit, then bh deploy in that folder
bh access <tool> members|listed|public [--tier viewer|editor]  members = anyone in the workspace (at that tier); listed = only shared emails; public = anyone on the internet may read it (a website), writes still need sign-in
bh secrets ls|set|rm <tool> NAME      set reads the value from stdin or a prompt; restarts the tool
bh users [add <email> --role member|owner|guest] [invite <email>] [rm <email>]   owners only
bh billing                       balance, burn per day, days left, card, last ledger lines
bh billing card                  optional page to replace or save a card for capped auto-refill
bh billing topup 20 [--yes]      quote, then secure Stripe checkout; the owner completes payment there
bh billing autorefill 20 --cap 100   capped refill; omitted --cap allows at most one refill amount per month
bh usage <tool>                   usage, hard limits, and any pause reason
bh capacity <tool> --gb 2         quote a higher storage limit; requires owner approval
bh capacity <tool> --yes --quote-id <id> --max-monthly-cents <approved-cap>
bh prices                        the price sheet in words
bh domain check <name>           availability and price
bh domain buy <name> [--yes --quote-id <id> --max-cost-cents <budget>] [--to <tool>]   quote first; confirm the same quote within the approved total; --to puts a tool on the domain's front
bh domain ls | attach <name> [--to <tool>] | point <name> --to <tool> | dns <name> | repoint <name> | primary <name> | detach <name>
                                 point = the domain itself and www. open that tool (a website on its own domain); tools also keep <tool>.<domain>
bh domain free                   wire <workspace>.boathousecloud.com (the free address)
bh registrar status|connect|wait|keys   host only: connect the registrar account (one approval click, nothing to copy)
bh keys create --name <what> [--for email]   project keys for agents; shown once. Owners, members, and anyone who is admin on a tool can hold one
bh rm <tool> [--purge --yes]     without --purge the database, files, sharing and secrets stay and all come back when a tool of that name is deployed again; purge deletes them
bh export [<tool>] [--dir .] [--all]   one .tar.gz per tool: source/, database.sql, data/, README.txt (secrets left out)
bh restore <tool> [--from YYYY-MM-DD] [--yes]   database and files back from a nightly backup (03:35 UTC); plan first, --yes to do it; a copy of the current state is kept
bh audit | bh whoami
bh update                        re-download bh from the platform; bh skill   refresh ~/.claude/skills/boathouse
```

## How to ship a tool end to end

1. Build it with a Dockerfile listening on `$PORT`, tables created at startup from `DATABASE_URL`.
2. `bh deploy` in the folder. Read the URL back. If it fails, `bh releases <tool>` shows the build or start log tail; fix and deploy again.
3. Secrets: `printf '%s' "$VALUE" | bh secrets set <tool> NAME -`. Never paste secret values into chat.
4. Sharing: new tools are visible to workspace members. `bh access <tool> listed` then `bh share` for anything sensitive. Pass on the invite link it prints.
5. Report the URL and who can see it. Do not open a browser; there is nothing to click.

## A website, not a tool

A public site (a portfolio, a research database, a landing page) is `bh deploy` in the folder with the `index.html`,
then `bh access <name> public` so anyone can read it, then `bh domain buy example.com --yes --quote-id <quoted-id> --max-cost-cents <approved-total> --to <name>` so it answers
at example.com and www.example.com with HTTPS. Reads need no sign-in; anything that writes still does.

## How to give a workspace its own domain

1. `bh domain buy acme.example` returns the exact total and a quote ID. Confirm with
   `bh domain buy acme.example --yes --quote-id <quoted-id> --max-cost-cents <approved-total>`.
   The prepaid workspace balance pays registrar cost plus Boathouse's margin. Reuse that quote ID after a timeout;
   the saved purchase identity prevents duplicate purchases or charges. A price over the approved limit is refused.
2. DNS is set by the same command (apex and wildcard A records). Certificates are issued on first visit.
   Allow a few minutes for DNS to spread. The first bought domain becomes the workspace's primary address.
3. A domain held elsewhere: `bh domain attach <name>` prints the two A records to make at that DNS host.

## Money, and the rule for spending it

A workspace holds prepaid credit; nothing runs until money is on it (a deploy on an empty balance is refused with a 402 that says where the owner adds money). `bh referral` shows the person's referral code, link, QR image URL, and dashboard at https://boathousecloud.com/partners. Partners earn 10% of referred customers' paid hosting and storage usage for their lifetime, including future apps/workspaces on that customer account. Free credit, unused deposits, domains, taxes, refunds and disputes are excluded. Monthly cash payouts start at $10; smaller balances roll over. Customers receive half-price organization hosting for their first 60 days. Partners can join free without deploying an app. The dashboard tracks earnings and manual completed transfers; it does not send money. The organization costs exactly $10.00 for a full UTC calendar month for up to five tools, divided into daily charges while any tool runs; adding more tools within the allowance does not multiply that charge. Static sites and stopped tools count toward the five-tool limit. The first GB of storage in each tool
is included, then $0.25/GB/month; a domain costs registrar price plus $2.00/year. At zero the workspace pauses
(tools stop, nothing is deleted) and resumes on the next top-up. **Every command that spends money returns a
quote first and does nothing else**: `bh domain buy x.example` and `bh billing topup 20` print the cost and stop;
the agent shows the cost to the person, and only on their yes runs the same command with `--yes`. Never add
`--yes` on your own. If the balance is short, do not try to charge the card yourself: tell the person the amount
and point them at their workspace page (https://boathousecloud.com/account), where Add credit opens one secure Stripe checkout, including any bank verification. Card details never go through chat. Auto-refill is a separate, explicit approval with a monthly cap; then continue.

## New workspace from the terminal

`bh signup "Acme Studio" owner@acme.example` creates a workspace and emails the owner a secure invitation. With `--code BH-XXXXXXXXXX`, it instead returns a signup link with the referral code filled in; the owner confirms their own email before the referral is attached. A referral makes every app half price for 60 days. After confirming their mailbox and choosing a password, the owner copies the connection from the welcome page. Agents that speak MCP connect to
https://mcp.boathousecloud.com/mcp (Streamable HTTP) with header `Authorization: Bearer <project key>`:

```
claude mcp add --transport http boathouse https://mcp.boathousecloud.com/mcp --header "Authorization: Bearer bh_…"
```

The MCP tools are listed under "Getting connected" above; `deploy` takes a map of file paths to contents, and
`domain_buy` first returns a `quote_id`; confirm with `confirm: true`, that same `quote_id`, and
`max_cost_cents` within the user's approved budget. Keep the same quote ID on retries. A purchased domain with
`dns_pending: true` needs `domain_repoint`, not another purchase. `topup` confirms with the `operation_id` from
its preceding quote, the same `cents`, and `confirm: true`. Reuse operation IDs on retries; the CLI carries them automatically.

## Gotchas

- `bh deploy` uploads everything in the folder except `.git`, `node_modules`, Python caches/environments, `.env`, `.env.*` (except example/sample/template files), and whatever `.dockerignore`
  lists; editor leftovers (`app.py.bak`, `notes.txt`) go up too and `bh pull` brings them back. Keep the folder clean or list them.

- The slug comes from `boathouse.json` or the folder name; keep it stable or you create a second tool.
- Every deploy replaces the container; anything not in `/data` or the database is gone. Files go under `DATA_DIR`.
- `bh secrets set` restarts the tool. `bh secrets rm` takes effect on the next restart.
- Rollback re-runs an earlier image with the current secrets; it does not touch the database. `bh restore` puts the database and files back from a nightly backup, and is a spending-style command: show the plan, act only on the person's yes (`--yes`).
- `bh export` is the whole tool in one file (code, database as SQL, files) and runs anywhere with a Dockerfile; secrets are not in it.
- Sessions are per domain: signing in on `acme.example` does not sign you in on the free address.
- The registrar's own rules: one purchase attempt per 10 seconds, no premium names by API, prepaid balance only.


## Resource limits and growth

On the managed service, an organization includes up to five lightweight tools for $10/month. All its app containers share 512 MB RAM and half a CPU core, enforced by one parent cgroup; these are not per-tool allowances. Each tool starts with 1 GB of combined database and file storage. A static website uses a tool slot. Lightweight means websites, forms, trackers, calculators and dashboards doing modest work in response to people. Large datasets, video processing, locally hosted AI models and continuously busy jobs need a reviewed plan. Calls to an external AI service may fit, but provider charges are separate. CPU work is throttled at the shared cap; memory exhaustion can kill/restart an app. Check `bh billing` for shared usage and `bh usage <tool>` for storage; do not promise a fixed number of visitors. At most five tools can be created per organization, even when some are stopped. Deleted tools with retained data keep their storage allocation; request owner approval before purging it. Apps write persistent files to DATA_DIR; other app files are read-only and temporary directories are bounded. Builds run separately, one at a time, with 1 GB RAM, one CPU, 3 GB temporary storage and a five-minute deadline. Uploaded source expands to at most 256 MB; app images are at most 512 MB and source history at most 512 MB per app.

Use `bh usage` before diagnosing a slow or paused app. Storage warnings appear in the account and are emailed to owners at 80% and 90%. An app near its storage limit pauses; its data remains. Native filesystem quotas enforce the hard limit even if the monitor is unavailable. PostgreSQL roles have 8 connections, bounded temporary query files and query timeouts. If capacity is unavailable on the host, a deployment is refused before replacing the current app.

Never silently upgrade an app. Ask for a capacity quote, explain its maximum extra monthly cost, and confirm only the exact approved quote and price. Self-service storage is capped at 10 GB and depends on available host capacity. Larger workloads need operator review. CPU and memory increases also need operator review. Never tell someone a deployment is queued unless the service actually accepted it.


## Domain renewals

Buying a domain also enables renewal from prepaid Boat House credit up to the quoted renewal limit. Show this ongoing renewal policy with the purchase quote. Boat House emails the price around 30 days before expiry, attempts renewal 14 days before expiry, and waits at least seven days after sending the price notice. A higher price requires owner approval; low credit or provider trouble stops the renewal and triggers a notice. Never fund the registrar account or charge a customer card on your own.

Owners can manage this without a terminal at their account's **Manage domain renewals** link, including detached domains. For agents, `bh domain renewals` (MCP `domain_renewals`) reads expiry, status, limits and errors. `bh domain renewal <domain> --enabled on --max-cost-cents <approved-total>` previews a change; add `--yes` only after approval. Use `--enabled off` to cancel future renewal, which lets the domain expire. MCP `domain_renewal_set` takes domain, enabled, max_cost_cents and confirm; preview before confirming. Detaching a domain does not turn renewal off. An in-flight renewal must finish reconciliation before its settings can change.
