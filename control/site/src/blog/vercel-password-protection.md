title: Vercel password protection costs $150 a month. Here are the cheaper ways to lock a site.
seo_title: Vercel Password Protection and Alternatives
slug: vercel-password-protection
description: Compare Vercel deployment protection with app sign-in. Understand access options and what to check when hosting a private team tool.
keyword: vercel password protection cost, vercel password protect site, vercel password protection free
date: 2026-09-08
category: Comparisons

> Password Protection on Vercel is part of the Advanced Deployment Protection add-on: $150 a month on Pro, included on Enterprise, not available on Hobby, with a 30-day minimum.
> The free option, Vercel Authentication, only admits members of your Vercel team, so every colleague needs a Vercel seat.
> A shared password is not a login. It cannot tell you who changed what, and it cannot remove one person.
> For a small team tool, Boat House gives each person their own login with a role for $10 a month a tool.

## The short answer

You cannot password-protect a Vercel site for free. [Password Protection](https://vercel.com/docs/deployment-protection/methods-to-protect-deployments/password-protection) lives inside the Advanced Deployment Protection add-on, which costs $150 a month on the Pro plan and is included with Enterprise. The Hobby plan does not offer it at all. Once you turn it on, there is a 30-day minimum before you can turn it off.

What you get for the $150 is one password for the whole deployment, plus private production deployments and protection exceptions. It is built for agencies hiding a client preview, not for a company tool with ten staff.

## What is free on Vercel

Standard Deployment Protection is free on every plan. It uses Vercel Authentication: preview deployments are visible only to people signed in to Vercel who belong to your team. That is fine for developers reviewing each other's branches. It is not fine for the bookkeeper, because the bookkeeper would need a Vercel account and a paid seat on your team to open the tool.

You can also protect a preview with a link that carries a token, but a link that carries a token is a password that lives in your chat history.

## A shared password is not a login

Even at $150, Password Protection gives you one string that everyone types. That means:

- You cannot see who did what. Every action comes from "the person with the password".
- You cannot remove one person. When someone leaves, you change the password and tell everyone else.
- You cannot give roles. The person who should only read has the same power as the one who should edit.
- The password ends up in Slack, in a sticky note, in a screenshot.

For a public site with a "coming soon" page, that is fine. For a tool that holds staff or customer data, it is the wrong tool at any price.

## Four cheaper ways to lock a small app

### Basic auth in middleware, free

If you are a developer, a few lines of Next.js middleware checking an `Authorization` header gives you HTTP basic auth on Vercel for nothing. It is still one shared password, and the browser prompt is ugly, but it works and costs nothing. You maintain it.

### Auth as a service (Clerk, Auth0, Supabase Auth), free tier then per user

Real accounts, real roles, good libraries. Free tiers cover a small team. You now maintain a user table, an invite flow, password resets and the role checks inside your app, and you have a second vendor with its own pricing cliff. Right for a product with customers; heavy for an internal tool.

### A VPS with a reverse proxy, $6 a month

Caddy or nginx with basic auth in front of the app. Cheapest in dollars, and now you run a server: TLS, updates, backups, the outage. Fine for developers who like that work.

### Boat House, $10 a month a tool

Boat House was built for the internal-tool case. Every tool sits behind Boat House sign-in, with named accounts and three tiers, viewer, editor and admin, and a Postgres database and file storage included. You do not write the auth; it is the front door of the platform.

- `bh deploy` in the project folder puts it live at its own address.
- `bh share tool teammate@example.com --tier viewer` emails Alex a one-time link; Alex sets a password and is in.
- `bh unshare tool teammate@example.com` removes Alex the day Alex leaves. Nobody else's password changes.
- `bh domain point company.com --to tool` puts it on your domain; `bh domain buy` buys one if you do not have it.

The price is [$10 a month a tool](/#pricing), no per-person charge, no card to sign up. Everything is a command, so Claude Code or Codex can do the deploy and the sharing without anyone opening a dashboard.

## Side by side

| | Vercel Password Protection | Vercel Authentication | Boat House |
|---|---|---|---|
| Monthly cost | $150 add-on on Pro ($20 seat), Enterprise included | Free, but a Vercel seat per viewer | $10 a tool |
| Who can open it | Anyone with the one password | Members of your Vercel team | Named people you invited |
| Roles | None | Vercel roles, not app roles | Viewer, editor, admin |
| Remove one person | Change the password for everyone | Remove their Vercel seat | `bh unshare` |
| Database included | No | No | Postgres, nightly backups |
| Minimum term | 30 days | None | None |

Vercel figures are from its documentation in September 2026; check the pricing page before you decide.

## Questions

### Is Vercel password protection free on the Hobby plan?

No. It is not available on Hobby at all, and on Pro it is the $150 a month Advanced Deployment Protection add-on.

### Can I password protect only one page on Vercel?

Password Protection applies to the deployment, not a page. For one page you would write middleware that checks a password on that path.

### What is the cheapest way to password protect a Vercel deployment?

Middleware with basic auth is free if you can write it. If you want real logins for a team, the cheapest option that includes them is Boat House at $10 a month.

### Can I keep the site on Vercel and put only the private tool on Boat House?

Yes, and that is the usual shape: the marketing site stays on Vercel, the internal tool moves to Boat House on a subdomain like `tools.company.com`.
