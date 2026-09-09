# Boat House

**Open-source infrastructure for small software built with AI agents.**

Boat House gives a tool a live HTTPS address, sign-in, a database, persistent files, and sharing by email. Editors use the app in a browser. Developers with Admin access can use their own agents to fetch its source and publish versioned updates to the same address.

The software is licensed under [Apache 2.0](LICENSE). You can run it on your own infrastructure or use [Boat House managed hosting](https://boathousecloud.com).

| | Self-host | Managed hosting |
|---|---|---|
| Software | Free under Apache 2.0 | Same open-source foundation |
| Infrastructure and operations | You provide and maintain them | Boat House operates them |
| Price | Your server, domain and provider costs | $10 per running tool per month; domain purchases are separate |
| Getting started | [Self-hosting guide](docs/self-hosting.md) | Sign up, confirm email, copy the connection into your agent |

## What is included

- A FastAPI control plane and Caddy HTTPS gateway.
- Account sign-in, email invitations, per-tool Viewer / Editor / Admin access, and signed identity passed to apps.
- Per-tool PostgreSQL databases, persistent file volumes, container resource limits, and saved source releases.
- Deployment, source checkout, updates, rollback, export, and backup/restore commands.
- The `bh` CLI, an MCP endpoint, and an agent skill for Claude Code, Codex and other compatible agents.
- Optional email, domain registration, and prepaid billing integrations. Self-hosters configure their own providers; metering can be disabled.

Admin is full control of a tool, including its source, secrets, sharing and deletion. Software collaboration uses source checkout and versioned publication. App code enforces its own record-level access rules.

## Start with managed hosting

Create an account at [boathousecloud.com](https://boathousecloud.com), confirm your email, then choose **Copy connection** and paste the message into your coding agent. Ask it to put a tool online or share it by email. Add credit before deploying. No separate hosting, database or sign-in project is needed.

## Run your own installation

See [the self-hosting guide](docs/self-hosting.md) for a Linux host with Docker, DNS and HTTPS. The public release contains no accounts, credentials, database snapshots, hosted customer tools or production configuration. All examples use placeholders; generate your own secrets.

This is infrastructure for trusted teams and trusted app builders. The control plane manages the host Docker daemon. Resource limits and container networks do not make it a secure sandbox for hostile code. Read [the security model](SECURITY.md) before exposing an installation.

## Development

Python 3.12 is used by the container image. In a virtual environment:

```sh
python -m pip install -r control/requirements.txt -r requirements-dev.txt
python -m pytest -q
python scripts/check_public_tree.py
```

Tests create temporary local state and use fake providers. Do not point tests at production credentials or state. CI runs the tests, publication hygiene checks, and Gitleaks.

## Repository layout

| Path | Purpose |
|---|---|
| `control/app/` | API, account UI, gateway authorization, deployment and providers |
| `control/site/` | Public-site source and static assets |
| `cli/bh` | Standard-library Python CLI |
| `skill/boathouse/` | Agent instructions |
| `caddy/`, `docker-compose.yml` | Gateway and services |
| `deploy/` | Host installation, synchronization and backup scripts |
| `examples/hello/` | Minimal app that checks signed identity |
| `tests/` | Regression tests with synthetic data |

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities using [SECURITY.md](SECURITY.md).
