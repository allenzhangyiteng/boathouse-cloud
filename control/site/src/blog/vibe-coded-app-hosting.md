title: How to host a vibe-coded app for your team, with logins
slug: vibe-coded-app-hosting
description: You built the tool in an afternoon with Claude Code, Cursor, Lovable or ChatGPT. Now six colleagues need to use it. Here is what hosting a vibe-coded app actually involves, and the cheapest ways to do it right.
keyword: vibe coded app hosting, deploy vibe coded app, host vibe coded app
date: 2026-09-08
category: Hosting

> Building the app is the easy half now. The hard half is a URL with a login that your colleagues can open on Monday.
> Free hosting works for public pages. The moment the app has staff data in it, you need real accounts, roles and a database that persists.
> Most vibe-coding tools charge a seat for every person who opens the app, or leave the login to you.
> Boat House hosts the app with named logins, a Postgres database and a domain for $10 a month a tool, and the agent that built the app deploys it.

## The short answer

A vibe-coded app is real software. It needs the same three things every app needs once more than one person uses it: a stable address, a login with roles, and storage that survives a restart. Free tiers give you the address. The login and the storage are where people get stuck, and where the bills start.

If you want the shortest path: keep the code, put it on a host that includes accounts and a database, and let the agent that wrote the app run the deploy. That is what Boat House is. If you want to understand the choices first, read on.

## Why "just deploy it" turns into a week

The typical story goes like this. Someone in a 12-person company builds a tracker or a scheduler with an AI coding tool. It works on their laptop. They deploy it to a free host and paste the link in the team chat. Then:

- A colleague opens the link on their phone and the data is gone, because the free database reset.
- Someone outside the company finds the link. There was no login.
- The builder changes something on Tuesday and the app is broken for everyone by lunch, because there is no staging and no rollback.
- Two months later the builder is on holiday and the app goes down. Nobody knows where it runs.

None of this is about the code. It is about the plumbing around the code, which is exactly the part the vibe-coding tools do not do well, because it is boring.

## What you actually need

| Need | Why | What "done" looks like |
|---|---|---|
| A stable URL | People bookmark it | `tool.yourcompany.example` or a subdomain |
| Named logins | Staff data, and knowing who changed what | Each person signs in as themselves |
| Roles | Not everyone should edit | Viewer, editor, admin |
| A real database | Data must survive restarts and deploys | Postgres with nightly backups |
| File storage | Uploads, PDFs, images | A volume that persists |
| Updates without fear | The tool will change weekly | Deploy, watch logs, roll back |
| An exit | Companies change tools | Export the code and the data in one file |

## The options for a small team

### Stay inside the builder (Lovable, Replit, Bolt, Base44)

The easiest path if the tool never leaves that platform. Each one can publish a URL and most offer some kind of login. The costs are per seat and per usage. Lovable's paid plans start at $25 a month and a published app can be limited to workspace members, which means each viewer is a member of your Lovable workspace. Replit's hosting is metered on top of the $25 Core plan. We covered [what a Replit deployment really costs](/blog/replit-deployment-cost) and [what Lovable's custom domain and login can and cannot do](/blog/lovable-custom-domain-and-login).

The bigger issue is that the tool is tied to the builder. If the platform changes its pricing, and all of them did in 2025 and 2026, the tool goes with it.

### Vercel or Netlify with a password

Great for public sites. For a private tool, Vercel's Password Protection is a [$150 a month add-on](/blog/vercel-password-protection) and gives you one shared password, not named accounts. A shared password is the Slack-link problem with one extra step.

### Your own server

A $6 VPS runs many small apps. Claude Code will happily write the Docker Compose file, the Caddy config and the backup script. Then you own it: TLS renewals, Postgres upgrades, the 6 a.m. outage. For a developer this is a fine trade. For the office manager who built the scheduler, it is not.

### Boat House

Boat House is a small cloud for exactly this software. From the same Claude Code session that built the tool:

- `bh deploy` puts the folder live at its own address, with a Postgres database and a persistent `/data` folder, behind Boat House sign-in.
- `bh share tool ana@company.example --tier editor` emails Ana a one-time link. Tiers are viewer, editor and admin.
- `bh domain buy company.example --yes` buys a domain and points it at the tool from the terminal.
- `bh logs`, `bh rollback`, `bh secrets set` cover the day-to-day.
- `bh export` gives you the whole tool, code and data, in one file, whenever you want to leave.

The price is [$10 a month a tool](/#pricing) with no per-person charge, and no card to sign up. Because every step is a command, the agent does the deploy without you opening a browser.

## About 90 seconds to connect your agent

1. [Create a workspace](/signup) and confirm your email.
2. Click **Copy connection** and paste it into your Claude Code, Codex, or Cursor chat. The agent installs what it needs and checks the connection; allow about 90 seconds for this step.
3. Add funds before the first deploy, then tell your agent: "Deploy this to Boat House and share it with Ana and Sam as editors."

The 90 seconds covers the agent connection. Email delivery, funding, app builds, and domain setup take extra time. Your agent handles deployment and sends your colleagues their sign-in links. Updates are the same sentence next week.

## The trust question

People ask, reasonably, why they should trust a small host with their staff data. Two answers. First, compare it with what you do today: a free deploy with no login, or a link with a shared password. Named logins with roles and nightly backups are safer than either. Second, you are never locked in: `bh export` gives you everything in one file, and `bh restore` puts it back on another workspace or another server.

## Questions

### What is a vibe-coded app?

An app built by describing it to an AI coding tool such as Claude Code, Cursor, Lovable, Replit Agent or ChatGPT, usually by someone who would not call themselves a developer. The code is real; the person just did not type it.

### Can I host a vibe-coded app for free?

Yes, if it is a public page with no accounts and you accept that the free database may reset. For a tool with logins and staff data, free tiers stop being free once you add a password feature, seats, or an always-on server.

### Does the app need a Dockerfile for Boat House?

No. A folder with a Dockerfile is built as is. A plain website folder is served as is. Claude Code writes the Dockerfile if one is needed.

### How do people sign in?

With an email and a password on Boat House sign-in. There is no Google or Microsoft account required, and no seat charge. Sharing sends a one-time link by email, like sharing a document.
