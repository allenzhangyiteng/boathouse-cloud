title: How much a Replit deployment costs in 2026, with the math for a small team tool
slug: replit-deployment-cost
description: Replit's hosting prices after the August 2026 cut, explained without the jargon: Autoscale, Reserved VM and Static deployments, what the Core plan credits cover, and what one always-on internal tool really costs per month.
keyword: replit deployment cost, how much does replit deployment cost, replit app hosting cost, replit alternatives for hosting
date: 2026-09-08
category: Comparisons

> Replit hosting is billed on top of the plan. Core is $25 a month ($20 yearly) and includes $25 of usage credit that hosting draws from.
> Autoscale deployments cost $2 a month plus $0.60 per million compute units and $0.40 per million requests. They sleep when idle.
> Reserved VMs are always on: $15 a month for 0.5 vCPU and 2 GB, then $35, $50 and $130 for bigger sizes.
> A small always-on team tool with a database lands around $40 to $60 a month all in. Boat House hosts the same tool for $10 with logins included.

## The short answer

Yes, Replit deployments cost money, but light use is often covered by the credits that come with the plan. The plan is the fixed part: Core is $25 a month, or $20 a month billed yearly, and it includes $25 of monthly credit. Hosting, database compute, storage and outbound transfer all draw from that credit. When it runs out you pay the usage rates below. Replit cut these rates in August 2026, so older blog posts overstate them.

## The three kinds of deployment

| Type | Price | Best for |
|---|---|---|
| Static | Free, plus $0.05 per GB transferred | Sites with no server |
| Autoscale | $2 a month base, plus $0.60 per million compute units and $0.40 per million requests; scales to zero when idle | APIs and apps with bursts of traffic |
| Reserved VM | $15 a month (0.5 vCPU, 2 GB), $35 (1 vCPU, 4 GB), $50 (2 vCPU, 8 GB), $130 (4 vCPU, 16 GB) | Anything that must be always on: bots, schedulers, apps with background jobs |

Figures are Replit's published prices as of August 2026. Check [replit.com/pricing](https://replit.com/pricing) before you decide; they have changed twice in a year.

## What "compute units" means for you

Autoscale meters CPU and memory time in compute units. A light internal tool that a dozen people open a few times a day uses very few, and the $2 base is most of the bill. A tool that polls an API every minute, runs a scheduled job, or keeps a websocket open never scales to zero and burns units all day. That is when a Reserved VM becomes cheaper and more predictable.

The practical rule: if the app has to be awake when nobody is looking at it, use a Reserved VM and budget $15 or $35. If it only needs to answer when someone opens it, Autoscale and a few dollars.

## Worked example: one internal tool for a 10-person company

The tool: a Python or Node app with a small Postgres database, a nightly job, ten people who open it during the day, one person who edits it.

| Line | Monthly |
|---|---|
| Core plan (the editor, the agent, the credits) | $25 |
| Reserved VM, smallest size, so the nightly job runs | $15 |
| Database and storage past the credits | $0 to $10 |
| Second editor on Teams, per seat | $40 |
| Total, one builder | $40 to $50 |
| Total, two builders | $80 to $90 |

Only the builder needs a seat. People who just open the app do not, and Replit Auth gives them a login for free. So the floor for one always-on tool is the plan plus the smallest VM: about $40 a month.

## Where Replit is a good deal

If you build in Replit and only ever deploy from Replit, the bundle is fair: agent, editor, hosting, auth and a database in one bill, and the credits absorb small projects. The August 2026 price cut made the hosting side reasonable.

## Where it stops being a good deal

- **Several small tools.** Each always-on tool needs its own Reserved VM. Three tools is $45 in VMs before the plan.
- **You did not build it in Replit.** If Claude Code or Cursor wrote the app, importing it into Replit just to host it adds a $25 plan for the privilege.
- **Usage you cannot predict.** Autoscale bills are hard to estimate for a background-heavy app, and the surprise bills are what people search for.
- **Leaving.** The database and auth are Replit's; moving out means rebuilding both.

## The alternative for tools built elsewhere

Boat House is a host for small software built by an agent. It has no builder of its own and does not need one: Claude Code, Codex or Cursor runs `bh deploy` in the project folder and the tool is live behind a login with a Postgres database and file storage. Sharing is an email: `bh share tool sam@company.example --tier editor`. A domain is `bh domain buy company.example --yes`. Leaving is `bh export tool`, one file with code and data.

The price is [$10 a month a tool](/#pricing), always on, no seats, no usage meter for ordinary use. For the ten-person example above that is $10 instead of $40 to $50, and for three tools it is $30 instead of roughly $85.

| | Replit, one tool | Boat House, one tool |
|---|---|---|
| Plan | $25 | $0 |
| Always-on hosting | $15 | included |
| Database | credits, then metered | Postgres included, nightly backups |
| Login for 10 staff | Replit Auth, free | named logins, viewer/editor/admin |
| Domain | bring your own | buy or point from the terminal |
| Monthly | about $40 | $10 |

## Questions

### Do Replit deployments cost money on the free plan?

Deployments require a paid plan. The Starter plan does not include publishing to a live URL.

### Does Replit charge when nobody uses my app?

Autoscale deployments scale to zero and cost the $2 base when idle. Reserved VMs are billed for the month whether or not anyone opens the app.

### What is the cheapest way to host a Replit app?

Static deployments are free if the app has no server. For anything with a backend, an Autoscale deployment inside the Core credits is the cheapest way to stay on Replit. Off Replit, Boat House is $10 a month with the login and the database included.

### Can I move my Replit app to Boat House?

Yes, if the code is in a folder: download it or push it to GitHub, then `bh deploy`. If the app uses Replit's database or Replit Auth, those parts get swapped for Boat House's Postgres and sign-in, which Claude Code can do in one session.
