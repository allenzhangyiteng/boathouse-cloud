title: Where to host an app built with Claude Code, and give your team a login
slug: where-to-host-claude-code-app
description: Claude Code writes the app but does not host it. Here are the real options in 2026, what each one costs for a small team with named logins, and which to pick.
keyword: where to host claude code app, claude code hosting options, can claude code host apps
date: 2026-09-08
category: Hosting

> Claude Code builds software on your machine. It has no hosting of its own, so the app you just made is not online until you put it somewhere.
> For a public website, Vercel or Netlify are fine and mostly free. The trouble starts when the app is for your staff and needs logins.
> Password protection on Vercel is a $150 a month add-on. Replit charges by usage. A VPS is cheap but you become the sysadmin.
> Boat House hosts the app with a login, a database and a domain for $10 a month, and Claude Code does the deploy itself.

## The short answer

Claude Code cannot host anything. It runs in your terminal, writes files, and stops. Anthropic does not sell app hosting, so "can Claude Code host apps" has a one-word answer: no. What you do next depends on who the app is for.

If it is a public website with no accounts, push it to Vercel, Netlify or Cloudflare Pages and you are done, usually for free. If it is a tool for your team, the thing you need is not hosting. It is a login, a database that survives restarts, and a way to give ten named people access without a meeting. That is where the free tiers stop being free.

## What "hosting" has to include for a team tool

An internal tool built in an afternoon with Claude Code usually needs five things before a colleague can use it:

1. A URL that stays the same.
2. A login, so the URL is not a secret you pass around on Slack.
3. Roles. Some people look, some change data, one person changes the software.
4. A database and file storage that persist.
5. A way to update it next week without breaking it for whoever is using it.

Most hosting comparisons only score the first item. The other four are where the cost and the hours go.

## The options, honestly

### Vercel or Netlify

Best for public sites and for developers who already know them. Deploys from GitHub, free tier is generous, custom domains are easy.

The catch for a team tool is the login. Vercel's Password Protection is part of the Advanced Deployment Protection add-on, which is [$150 a month on the Pro plan](https://vercel.com/docs/deployment-protection/methods-to-protect-deployments/password-protection) and not available on Hobby. Vercel Authentication (free) only lets members of your Vercel team in, which means a Vercel seat for every colleague who wants to open the tool. Netlify's Password Protection is similar: a paid feature on business tiers, or you write the auth yourself. If you write the auth yourself you also write the user table, the invite flow, and the password reset.

### Replit

Replit can build and host in one place, and Replit Auth gives you a login in a checkbox. Hosting is billed by usage on top of the $25 a month Core plan: Autoscale deployments start at $2 a month plus compute and request charges, and a Reserved VM starts at $15 a month for the smallest size. We wrote up [the full Replit deployment cost math](/blog/replit-deployment-cost) separately. The honest summary is that a small always-on tool with a database lands around $40 to $60 a month once the plan and the VM are added, and everyone on your team who edits the app needs a seat.

### A VPS (DigitalOcean, Hetzner, Linode)

Five to twelve dollars a month for a box that can run a dozen small apps. This is the cheapest option by far and the one that costs the most hours. You set up TLS, a reverse proxy, Postgres, backups, a login layer, and you are the one who gets the call when it goes down. Claude Code is good at writing the config, but somebody still has to own the box. For a solo developer this is fine. For a business owner who built the tool with plain English, it is not.

### OpenAI Codex Sites

If your company is on ChatGPT Business or Enterprise, Codex Sites hosts full-stack JavaScript apps with Sign in with ChatGPT and workspace-only access. It is free during the preview. The limits are real: every viewer needs a paid ChatGPT seat, there are no custom domains, and it runs JavaScript only, so a Python app Claude Code wrote does not fit.

### Boat House

Boat House is built for exactly this case: a tool built by an agent, for a small team, that needs a login. Claude Code runs `bh deploy` in the folder and the tool is live at its own address behind Boat House sign-in, with a Postgres database and file storage. `bh share finance nancy@acme.example --tier editor` sends her an email with a one-time link; there are three tiers, viewer, editor and admin. `bh domain buy acme.example --yes` buys and points a domain from the terminal. The price is [$10 a month a tool](/#pricing) and there are no per-person charges. The [docs](/docs) list everything the command can do.

## Side by side for a ten-person team

The scenario: one internal tool, Python or Node, a small database, ten named people who need to open it, one person who updates it.

| Option | Monthly cost | Login for 10 people | Database | Who does the ops |
|---|---|---|---|---|
| Vercel Pro + Password Protection | $20 seat + $150 add-on | One shared password | Bring your own | You |
| Vercel with Vercel Authentication | $20 per Vercel seat, times 10 | Team members only | Bring your own | You |
| Replit Core + Reserved VM | $25 + $15 and up | Replit Auth, free | Included, metered | Replit, mostly |
| VPS | $6 to $12 | Write it yourself | Install it yourself | You |
| Codex Sites | $0 in preview, seats required | ChatGPT accounts | Included (JS only) | OpenAI |
| Boat House | $10 | Named logins, three tiers | Postgres included | Boat House |

Prices are the vendors' published figures in September 2026; check their pages before you decide.

## How the Boat House deploy looks from Claude Code

There is no dashboard to click through. You say, in the Claude Code session that built the tool, "put this online and share it with Sam as an editor". The agent runs:

- `bh deploy` in the project folder. Boat House builds it from the Dockerfile, or from a plain website folder if there is no Dockerfile, gives it Postgres credentials, and puts it at `tool.workspace.boathousecloud.com`.
- `bh share tool sam@acme.example --tier editor`. Sam gets an email with three steps and a one-time link.
- `bh logs tool` when something goes wrong, `bh rollback tool` to go back a release.

Everything a person can do, the agent can do, so the build does not stop because a human has to open a browser.

## When not to use Boat House

If your app is public and has no accounts, a static host is cheaper. If you expect thousands of users, you want a platform that scales horizontally. If you already run a VPS happily, keep running it. Boat House is for the software that is too small for a devops team and too important to share as a Vercel link with a password in the Slack channel.

## Questions

### Can Claude Code deploy an app by itself?

Yes, to any host with a command-line interface. On Boat House the whole path is one command, `bh deploy`, and the [boathouse skill](/skill.md) teaches Claude Code the rest, so you can ask for the deploy in plain English.

### Does Claude Code host websites?

No. Claude Code produces files. Hosting is a separate service you choose. Anthropic's own hosted option, Claude artifacts, is for small shared pages rather than server applications with their own databases.

### What is the cheapest way to host a Claude Code app with a login?

A VPS is the cheapest in dollars and the most expensive in hours. Boat House at $10 a month is the cheapest option that includes the login, the database, backups and the domain purchase without any of the setup.

### Can I move my tool off Boat House later?

Yes. `bh export tool` gives you one file with the code, the database as SQL and the uploaded files. There is nothing proprietary in the app itself.
