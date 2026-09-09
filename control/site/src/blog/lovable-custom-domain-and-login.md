title: Lovable custom domain and login: what it costs, what it cannot do, and the alternative
slug: lovable-custom-domain-and-login
description: A plain guide to putting a Lovable app on your own domain and behind a login in 2026: which plan you need, what workspace-only access really means for your colleagues, and when to host the app elsewhere.
keyword: lovable custom domain, lovable login with username and password, lovable app hosting
date: 2026-09-08
category: Comparisons

> A custom domain on Lovable needs a paid plan, Pro at $25 a month or Business at $50. The domain itself is not charged extra.
> Lovable's published apps can be public or limited to your workspace. "Workspace" means your colleagues must be members of your Lovable workspace.
> Username-and-password login for your own users is something you build inside the app, usually with Supabase Auth. It is not a switch.
> If the app is an internal tool for a small team, hosting it on Boat House gives it named logins with roles, a Postgres database and a domain for $10 a month, and you keep the Lovable code.

## The short answer

You can put a Lovable app on your own domain on any paid plan. You can also stop strangers from opening the published app by setting website access to your workspace. What you cannot do with a switch is give ten colleagues their own username and password without either making them Lovable workspace members or building an auth system inside the app.

That last part is what most people searching for "lovable login with username and password" want, and it is the part this guide is about.

## Custom domains on Lovable

As of 2026, connecting a custom domain requires a paid plan. The [Pro plan](https://lovable.dev/pricing) is $25 a month (about $21 a month billed yearly) and Business is $50 a month. There is no separate charge for the domain feature; you bring a domain you already own, add the DNS records Lovable shows you, and the app is served on it with TLS.

Common snags people search for:

- **"lovable custom domain not working"**: almost always DNS. The records take time to propagate, and if the domain sits behind Cloudflare with the proxy on, the certificate step fails until you turn the orange cloud off for those records.
- **"lovable custom domain free"**: not on the free plan. The free tier publishes to a `lovable.app` subdomain only.
- **"lovable custom domain ssl"**: issued automatically once DNS resolves. If it does not, it is the DNS again.

## Who can open the published app

Lovable separates two things. Project access controls who can open the editor and the code. Website access controls who can open the published URL. Since December 2025 the default for new projects is workspace-only, and since April 2026 fully public projects are gone from the editor side.

For the published website, the choices are, in Lovable's own terms, Public or Workspace. Workspace means a visitor has to be signed in to Lovable and be a member of your workspace. On Business and Enterprise plans you can restrict further to invited users.

That works for a two-person company. For a 15-person office it means 15 Lovable accounts and 15 workspace seats for people whose only job is to open a tracker.

## Username and password for your own users

If you want your users to sign up with an email and password inside the app, the standard route is Supabase Auth, which Lovable wires up when you ask for it in the prompt. It is a real auth system and it works. What you take on:

- You now own a Supabase project, with its own free tier limits and its own pricing once you pass them.
- Roles (viewer, editor, admin) are yours to design and test. Row-level security policies are easy to get wrong and the failure mode is silent: someone sees data they should not.
- Password resets, invites, and removing a departed employee are all features you have to build.
- If you later move the app, the auth moves with it, which is good, but it means the app is now three services: Lovable, Supabase, and your domain registrar.

None of this is a criticism of Lovable. It is a product for building apps, and it is good at that. The plumbing for a private team tool is a different job.

## The alternative: keep the code, change the host

Lovable lets you export the code to GitHub. Once it is in a folder, the app is just a web app and can be hosted anywhere. On Boat House, the deploy is one command from Claude Code, Codex, or any terminal:

- `bh deploy` puts the folder live behind Boat House sign-in, with a Postgres database and file storage. If the app has a Dockerfile, it is built; a plain static site is served as is.
- `bh share app maria@company.example --tier viewer` emails Maria a one-time link. She signs in with her own email and password. No Lovable seat, no Google account.
- `bh domain buy company.example --yes` buys and points a domain from the terminal, or `bh domain point` uses one you have.
- `bh export app` gives you the code and the data in one file if you ever want to leave.

The price is [$10 a month a tool](/#pricing), no per-person charge, no card to sign up.

## Cost for a 15-person office, side by side

| | Lovable, workspace-only | Lovable + Supabase Auth | Boat House |
|---|---|---|---|
| Build plan | Pro $25 | Pro $25 | Keep using Lovable, or Claude Code |
| Viewers | 15 workspace seats | Free, but you build the auth | Free, named logins included |
| Roles | Editor-level only | Yours to build and test | Viewer, editor, admin |
| Database | Supabase | Supabase | Postgres included, nightly backups |
| Domain | Included on paid plan | Included on paid plan | Buy or point from the terminal |
| Monthly for hosting the tool | Seats add up fast | $0 to $25 (Supabase) | $10 |

Check the vendors' pricing pages before you decide; these are the published figures in September 2026.

## Questions

### Does Lovable charge extra for a custom domain?

No. The domain feature comes with any paid plan. You pay your registrar for the domain itself.

### Can I password-protect a Lovable app?

Not with a single shared password. You can limit the published app to your workspace members, or build user accounts inside the app with Supabase Auth.

### Can I move a Lovable app to Boat House?

Yes. Export the project to GitHub, clone it, and run `bh deploy` in the folder. If the app uses Supabase for data, it keeps working; if you want the database on Boat House, Claude Code can point it at the Postgres that Boat House provides.

### Will my Lovable app still work if I keep editing it in Lovable?

Yes. Publish from Lovable, pull the code, `bh deploy`. Or ask your agent to do the three steps whenever you say "ship it".
