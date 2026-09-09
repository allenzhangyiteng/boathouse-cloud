# Self-hosting Boat House

The source code is free under Apache 2.0. You pay for your own server, domain and optional providers. Boat House managed hosting is a separate paid service.

These instructions are for an operator comfortable with Linux, Docker, DNS and backups. People using the resulting apps only need their browser; developers can connect their agents.

## Prerequisites

- A dedicated Linux host with Docker Engine and Docker Compose installed, plus Git and OpenSSL.
- A domain you control, with its apex and a wildcard A record pointing at the host’s public IPv4 address. Open TCP 80 and 443; restrict SSH appropriately.
- Sufficient memory/disk for the control plane, PostgreSQL and the apps you run. Apps receive resource limits; capacity depends on their workloads.

## Install

Clone this repository to `/srv/boathouse` on your host. Copy `.env.example` to `.env`, make it readable only by the operator, and edit it:

```sh
cd /srv/boathouse
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

Put the generated value in `BH_PG_PASSWORD`. Replace the example domain, IP and owner email with your own values. `BH_WORKSPACE` is the first workspace slug; `BH_OWNERS` is a comma-separated list of its owners. `BH_METER=0` disables the hourly metering loop for self-hosting. Leave it off unless you intentionally operate a prepaid service.

Start the services:

```sh
mkdir -p /var/lib/boathouse/state /var/lib/boathouse/backups
chmod 700 /var/lib/boathouse/state /var/lib/boathouse/backups
docker compose -p boathouse up -d --build
docker compose -p boathouse ps
```

Caddy issues certificates on demand for hostnames recognized by Boat House. Check `https://api.YOUR_DOMAIN/api/health` after DNS resolves. The first request to a new hostname may take longer while its certificate is issued.

Docker’s default address pools allow only a limited number of isolated bridge networks. For a larger installation, configure suitable `default-address-pools` in Docker’s daemon configuration before launching apps. Preserve any existing daemon settings. `deploy/install.sh` is a helper for a fresh dedicated host; inspect it before use because it changes Docker daemon settings.

## First owner

Create a one-time invitation for the owner email configured in `.env`:

```sh
docker compose -p boathouse exec control python -m app.bootstrap invite owner@example.com
```

Replace the example with your actual configured owner address. Open the printed link privately, set your password, and sign in. The invitation is a credential: do not commit it, put it in logs, or share it publicly. Open the account page and choose **Copy connection** to connect your agent to this installation.

Public signup and email-based sharing require a working email provider. Configure your own provider before inviting other people.

## Optional providers

The software includes integrations; no provider accounts or credentials are bundled.

- **Email:** configure Resend or SMTP using `bh mail set` and credentials in the supported environment variables. Run `bh mail status` to inspect configuration. Test mail only to an address you control. `bh mail --help` and [the agent guide](../skill/boathouse/SKILL.md) describe the commands.
- **Domains:** the host workspace owner configures their own Porkbun API access. Quotes and confirmed purchases use that provider account and its funding. Domain registration and renewal are separate costs.
- **Payments:** prepaid billing is optional for a self-hosted installation. Operators offering paid service supply their own Stripe secret and webhook signing secret using `bh billing stripe-keys`; the webhook endpoint is `https://api.YOUR_DOMAIN/stripe/webhook`. Never place provider keys in the repository.

A tool needs sufficient ledger credit if metering is enabled. Disabling the hourly meter alone does not bypass deploy-time balance checks: self-hosters can grant local workspace credit with the host-owner `bh billing grant starter 100 --memo "Self-hosted local credit"` command. This updates the local ledger; it does not pay Boat House. Check `bh billing --help` for arguments.

## Updates and backups

Back up the control state, its master key, PostgreSQL databases and app file volumes before upgrading. `deploy/backup.sh` creates sensitive backup archives under `/var/lib/boathouse/backups` and can copy them to your own object storage. Schedule and monitor it yourself; installing the repository does not automatically configure a nightly scheduler or offsite retention.

Keep `.env`, provider settings, state and backup files out of Git. Review release changes, fetch the new source and run `docker compose -p boathouse up -d --build`. Retain a restorable backup and the previous image until verification is complete. Test restore procedures on a separate installation.

Before operating a public service, customize the bundled hosted-service terms, privacy page, support contact text, branding and pricing for your installation. The Apache license governs the source code; it is separate from the terms you offer to your own users.
