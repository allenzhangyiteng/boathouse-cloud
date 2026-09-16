title: Replit custom domain: how to connect one, and what it will not do
seo_title: Replit Custom Domains: Setup and Access
slug: replit-custom-domain
description: Connect a custom domain to a Replit app. Understand DNS setup, HTTPS, and the separate decisions about app logins and team access.
keyword: replit deploy custom domain, replit deploy to custom domain, replit deploy on custom domain
date: 2026-09-08
updated: 2026-09-16
category: Hosting

> The Domains tab only appears after a successful deployment. You publish first, then point the domain at the published app.
> You add one A record and one TXT record at your registrar. The TXT record has to stay there forever, because Replit uses it to renew the certificate.
> Linking `example.com` does not serve `www.example.com`. Each hostname is its own entry with its own records.
> A domain makes an internal tool easy to reach. It does not make it private. Anyone who types the address gets in unless you also build a login.

## The short answer

Deploy the app, open the Publishing tool, go to the **Domains** tab, choose to connect a domain you own, and copy the `A` record and the `TXT` record Replit gives you into your registrar's DNS settings. Verification usually takes minutes, though DNS can take up to 48 hours to spread worldwide. Replit issues the TLS certificate itself once the records resolve. Those steps are in [Replit's own custom domain docs](https://docs.replit.com/features/publishing/custom-domains).

The part that catches people out is not the DNS. It is what happens the day after: the tool is now on a memorable address that anyone can type, and Replit's domain feature has nothing to say about who is allowed in.

## Linking a domain you already own

The order matters. Publish the app first. Replit's docs are explicit: "The Domains tab becomes available once your app has a successful Deployment." If you are looking for the tab and cannot find it, that is why.

Then, in the Publishing tool's Domains tab, you have two routes:

- **Guided setup.** You authorise Replit to talk to your DNS provider and it writes the records for you.
- **Manual setup.** You enter the domain, Replit shows an `A` record and a `TXT` record, and you paste both into your registrar.

Four things go wrong often enough that Replit documents them:

- **Leftover records.** Keep exactly one `A` record for the hostname and delete any `AAAA` record sitting on it.
- **Cloudflare's orange cloud.** Set the records to DNS only. Proxy mode blocks the certificate step.
- **Deleting the TXT record once it works.** The `replit-verify=` record is not a one-time setup artifact. It has to stay for the life of the domain or the certificate renewal fails months later, which is a miserable thing to debug.
- **Assuming www comes along.** It does not. Add `www.example.com` as a separate entry with its own records if you want both to work.

You can also buy the domain inside Replit. [Replit announced domain purchasing in July 2025](https://replit.com/blog/domain-purchasing-on-replit): search for a name, pay with the card already on file, and the DNS is configured for you. The announcement does not publish the registration prices, so check the quote in the Domains tab before you buy.

## The plan you need

Replit's custom domain docs do not name a plan, but they do require a published deployment, and that is where the plan comes in. [Replit's pricing page](https://replit.com/pricing) shows Starter, the free plan, publishing one live project. Core is $20 a month, or $17 a month billed yearly, and includes $20 of monthly credit. Those are the figures as of September 2026. Replit [cut Core from $25 to $20 in February 2026](https://replit.com/blog/pro-plan), so older guides quote the higher number.

The plan is only the fixed part. Hosting is billed on top of it, by deployment type, and an always-on tool on a Reserved VM adds more. We worked that arithmetic out in [how much a Replit deployment costs](/blog/replit-deployment-cost).

## What the domain does not do

This is the honest part, and it is the same on every host of this shape.

A custom domain is a routing change. It tells the internet that `tools.yourcompany.com` means this app. It says nothing about who may open it. If the tool holds a client list, a rota, or anything you would not post publicly, you now have a nicely branded public URL for it.

Replit does offer an authentication feature you can add to the app, and it works. What you are signing up for is a build task rather than a setting: someone has to add the login, decide who counts as an admin, decide what a viewer may see, and handle the day a person leaves. For a team of five who wanted a shared tracker on their own domain, that is a project on top of a project.

Nor does the domain keep the app awake. Pointed at an Autoscale deployment, it still hits a cold start after an idle spell, which people read as "the site is broken".

## Side by side

| | Replit, custom domain | Boat House |
|---|---|---|
| Buy a domain | In the Domains tab, price quoted there | `bh domain buy company.com --yes`, price quoted before you pay |
| Point one you own | A record plus a permanent TXT record | Point it from the terminal |
| Certificate | Issued by Replit, renews via the TXT record | Issued and renewed for you |
| www and apex | Two separate entries | Both served |
| Who can open it | Public unless you build a login | Named logins, viewer, editor or admin |
| Plan before hosting | $20 a month Core | none |
| Hosting one always-on tool | Deployment billed on top of the plan | $10 a month per organization for up to five lightweight tools |

## The Boat House way

Boat House hosts small software built by an agent, so the domain and the login are the same job rather than two.

- `bh deploy` in the project folder puts the tool live behind Boat House sign-in, with a Postgres database, file storage and nightly backups.
- `bh domain buy company.com --yes` buys the domain from the terminal and attaches it. You see the price before it charges you. If you already own the name, you point it instead.
- `bh share tracker teammate@example.com --tier viewer` emails Sam a one-time link. Sam signs in with a named login. No seat, no Replit account, and `--tier editor` or `--tier admin` when they need more.
- `bh rollback tracker 1` puts yesterday's version back, and `bh logs tracker` shows what happened.
- `bh export tracker` gives you the code and the data in one file if you ever want to leave.

The price is [$10 a month per organization for up to five lightweight tools](/#pricing), no card to sign up, no per-person charge. A referral code makes organization hosting half price for the first 60 days. If the app was built in Replit, export the code and run `bh deploy` in the folder; the same trick works for a [Lovable app on a custom domain](/blog/lovable-custom-domain-and-login).

## Questions

### Why is the Domains tab missing in my Replit app?

Because the app has not been published yet. Replit's docs say the tab appears once the app has a successful deployment, so deploy first. If you have deployed and still cannot see it, check that you are in the Publishing tool for that app rather than in workspace settings.

### Can I put a Replit deployment on a custom domain for free?

Not really. The free Starter plan publishes one live project, and hosting past that means a paid plan plus the deployment's own bill. The domain feature itself is not charged separately; you pay your registrar for the name, whether you buy it inside Replit or elsewhere.

### My custom domain is verified but the site does not load. What now?

Work through the four usual suspects: one `A` record only with no stray `AAAA` on the same hostname, Cloudflare set to DNS only rather than proxied, the `replit-verify=` TXT record still present, and the apex and `www` added as separate entries. If all four are right, it is propagation, which Replit says can take up to 48 hours.

### Does a custom domain make my internal tool private?

No. A domain changes the address, not the door. If ten people should see the tool and nobody else should, you need a login in front of it, which on Replit means adding an auth system to the app. On Boat House the login is there from the first deploy and `bh share` adds a person by email.
