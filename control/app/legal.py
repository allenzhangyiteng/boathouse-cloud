"""The two pages every service needs, in plain words. Markdown, rendered by pages.legal. Dates are the last change."""
UPDATED = "September 16, 2026"
OPERATOR = "Boathouse Cloud"          # confirm the legal entity name once the company is formed

TERMS = f"""
**Plain-English summary, not a substitute for the terms below.** You put a small tool online with us and share it with people you choose. You keep everything you make. You pay $10 a month per organization for up to five lightweight tools, from a balance you top up yourself; nothing charges your card without your click, and your agent asks before anything costs money. Don't use us for anything illegal or harmful. We do our best to keep things running and backed up, but we are a small service and offer no guarantees. Either side can end this at any time; you can export everything first.

## 1. Who we are and what this covers

Boat House ("Boat House", "we", "us") is operated by {OPERATOR}. These terms cover boathousecloud.com, the `bh` command, the hosted MCP endpoint, and every tool we host for you (together, the "Service"). By creating a workspace, using a key, or opening a tool we host, you agree to them. If you use the Service for a company, you confirm you may bind that company, and "you" means the company too.

## 2. Accounts and keys

One account per email address. You are responsible for your password, for the keys your agent uses, and for what is done with them. Keys are tied to you, not to a workspace: a key works in every workspace you belong to, at the level you have there. Tell us at once if you think a key or password has leaked; revoke keys yourself from your account page or with `bh keys`. You must be at least 18 to open a workspace. People you share tools with may be younger, but not under 13, and you are responsible for their use.

## 3. Your tools and your content

Everything you or your agent put in a tool is yours: the code, the data inside it, the files, the domain names you buy through us. We claim no rights to it beyond what we need to host it, back it up, show it to the people you share it with, and keep it safe. You can take all of it out at any time with `bh export`. You are responsible for having the right to use what you put in a tool, and for what the tool does.

## 4. Sharing and the people you let in

You decide who can see or change each tool, at three levels: viewer, editor, admin. Anyone you share with must accept these terms to sign in. An admin can change the software of that tool, so share admin the way you would hand over a key. You can take access back at any time. When someone asks for access, only an owner of the workspace or an admin on that tool can allow it.

## 5. What you may not do

Do not use the Service to break the law, to host malware, phishing, spam, or scraped personal data you have no right to hold, to mine cryptocurrency, to attack other systems, to get around the limits of your plan, or to resell hosting to others as if it were your own. Do not try to reach other workspaces' data or our systems beyond what the Service offers you. We may pause or remove a tool that does these things, and we will tell you when we do unless the law stops us.

## 6. Money

- **Prepaid balance.** You put money on a balance and the Service draws from it. Nothing runs until there is money on the balance; signing up, connecting an agent and looking around are free.
- **Prices.** $10 per organization for a full UTC calendar month, divided into daily charges while at least one tool runs. Up to five tools are included; static websites and stopped tools count toward that allowance. Each tool includes 1 GB of combined database and file storage. More storage requires approval and costs $0.25 per used extra GB per month. Hosting is not charged while every tool is stopped; an existing charge for today is not reversed. Domains cost the registrar price plus $2 a year. People you share with have no seat charge. External AI and other third-party services are separate. Current prices are available at `bh prices` and on the pricing page; we give at least 30 days’ notice before raising them.
- **Lightweight tools and limits.** Small websites, forms, trackers, calculators and dashboards that respond to ordinary use. All tools in an organization share 512 MB of runtime memory and half a CPU core. Large processing jobs, model hosting and continuously busy apps need reviewed capacity. CPU is capped; exhausting memory can stop or restart an app. Full storage refuses writes and pauses the affected app without deleting its existing data. No capacity increase or associated charge is automatic. Saved data from deleted tools still uses capacity until its owner approves permanent removal. Source uploads, builds and databases also have the documented technical limits; host admission may pause new deployment when capacity is full. Contact support for a larger plan.
- **Your card is charged only on your click.** Adding a card charges nothing. A top-up happens when you press the button on your account page. If you turn on auto-refill, we charge the amount you set whenever the balance runs low, and never more than the monthly cap you set. You can turn it off at any time.
- **Your agent cannot spend on its own.** Before anything costs money beyond the running tools (a domain, extra storage, a top-up), the Service quotes the price and waits for your yes. The only charge that runs without a question is the daily metering of tools you already have running.
- **When the balance runs out**, tools pause and nothing is charged. Put money on the balance and they resume. We keep a paused workspace's data for at least 30 days before we may remove it.
- **Refunds.** Unused prepaid money that you put on the balance yourself is refundable on request within 30 days of the top-up. Domain registrations and metered usage are not refundable. Write to the address at the end.
- Payments are processed by Stripe under its own terms; we never see or store your card number.

## 7. Referrals

Joining the partner program is free. Your dashboard at `/partners` provides your referral link, QR code, and signup code. A new customer must use your code when creating their first account/workspace. The first valid referral remains attached to that customer account, including future apps and workspaces it owns; it cannot be replaced with another partner's code. Self-referrals are not eligible.

You earn **10% of that customer's paid hosting and storage usage for their lifetime**, for as long as they keep using and paying for Boat House. The customer receives half-price organization hosting for their first 60 days; adding another workspace does not restart that period. Commissions are based on actual usage paid with verified customer funds after discounts, not unused prepaid deposits. Free/promotional credit, domain purchases, taxes, refunds, and disputed payments do not earn commission. Fractional cents carry forward across your referred customers. Refunds or disputes reverse the related earnings; if those earnings were already paid, the adjustment carries forward against future commissions.

We review cash payouts monthly, by a transfer method agreed with you. Amounts below $10 roll over. The dashboard tracks earnings and completed transfers; it does not automatically send money. Contact support from your partner account email to arrange your payout method. Do not email bank details. We may request identity and tax information through an appropriate secure channel before paying; you are responsible for taxes on your earnings.

Disclose your financial relationship clearly when recommending Boat House. Do not mislead people, spam, buy or sell codes, create fake customer accounts, or bypass attribution rules. We may withhold or reverse fraudulent commissions. Changes to the rate or the end of enrollment apply only to future referrals after at least 30 days' notice: existing eligible referrals retain their 10% lifetime share. Earned legitimate commissions remain payable.

## 8. Domain names

When you ask your agent to buy a domain, we register it through our registrar in the workspace's name, with our contact details on file to keep your own private. The domain is yours: you can point it elsewhere, transfer it out, or let it lapse. Renewals are charged from your balance and we tell you before each one. Registry rules apply to every domain.

## 9. Availability, backups and support

We back every tool's database and files up every night and keep those backups for 30 days. We keep the Service running as well as we can, but we are a small service and do not promise a level of uptime. We may take things down briefly for maintenance. Support is by email at the address at the end, answered within one business day, and through your agent, which can read a tool's logs and roll it back to any earlier version.

## 10. Changes to the Service and to these terms

We improve the Service often. We will not remove a feature you depend on without notice. If we change these terms in a way that matters, we email the address on your account at least 14 days ahead; using the Service after that date means you accept the change.

## 11. Ending

You can stop any time: switch tools off, export your data, and ask us to close the workspace. We can end your access if you break these terms, with notice where we can give it, and for any other reason on 30 days' notice. Either way you can export everything for 30 days after.

## 12. No warranty, limited liability

The Service is provided as is. To the extent the law allows, we make no warranties, and our total liability to you for anything arising from the Service is limited to the amount you paid us in the 12 months before the claim. We are not liable for indirect losses, lost profits, or lost data beyond our backup promise above. Some places do not allow these limits, in which case they apply as far as they can.

## 13. The rest

These terms are governed by the laws of the State of Delaware, United States, without regard to its conflict-of-law rules, and disputes go to the courts there. If a part of these terms cannot be enforced, the rest still applies. You may not transfer your account; we may transfer the Service to a successor that keeps these promises. This is the whole agreement between us about the Service.

## Contact

[support@example.com](mailto:support@example.com). Answered within one business day.
"""

PRIVACY = f"""
**In one paragraph.** We collect what we need to run your workspace: your email, a password we store only as a hash, the name of your workspace, and whatever you put in your tools. Payments go through Stripe; we never see your card number. We do not sell data, run ads, or use tracking scripts. Your tools live on our servers in New York, backed up nightly for 30 days. You can export or delete everything yourself.

## What we collect

- **Account.** Email address, a hashed password, your workspace names, the keys your agent uses (stored hashed), and when you last signed in.
- **Referrals.** Your referral code, customer-account attribution, and the earnings we owe and have paid you, with the payout reference. Partners see anonymized referral counts and earnings, not customer names, email addresses, private apps, or app data. Codes and QR images are public so partners can share them.
- **Money.** A Stripe customer ID and the last four digits and brand of your card, so you can recognise it. Stripe holds the card itself. Your balance and a ledger of every charge and top-up.
- **Your tools.** The source code, database, files and settings of each tool, and its nightly backups. This may include personal data about the people who use the tool; you decide what goes in.
- **People you share with.** Their email address, the level you gave them, and their own hashed password once they set one.
- **Domains.** Domain names you buy through us and their DNS records. The registrar records our contact details on the domain so yours stay private.
- **Logs.** Our servers and your tools write logs (requests, errors, deploys) that include IP addresses and timestamps. We keep them for a short time for debugging and security, typically days, and an audit trail of actions in your workspace (who deployed, shared, or bought what) for as long as the workspace exists.
- **Email.** When we send an email on your behalf (a share, an invite, a request), we record that it was sent, to whom, and whether it bounced.

We do not collect analytics about your browsing, and there are no third-party scripts on our pages.

## How we use it

To run the Service: sign you in, host and back up your tools, meter and charge your balance, send the emails you trigger, register domains, answer your questions, and keep the Service safe from abuse. We do not sell it and do not use it to advertise to you.

## Who else sees it

Only the companies that help us run the Service, each under its own privacy terms and only for that purpose: **DigitalOcean** (servers and backup storage, New York), **Stripe** (payments), **Resend** (sending email), **Porkbun** (domain registration), and **Let's Encrypt** (HTTPS certificates). We share data with the law only when the law requires it, and we tell you when we are allowed to.

## The people who use your tools

If you build a tool that holds data about other people, you are responsible for that data under whatever privacy law applies to you, and we process it on your instructions. We keep each tool's database separate from every other tool's. If you need a data processing agreement, ask.

## Cookies

We set only the cookies that keep you signed in: one on boathousecloud.com for your account, and one on each tool's own address. They last up to a year and hold a random session ID, nothing else. Signing out removes them. No advertising or tracking cookies.

## Where it lives and for how long

Your data lives on servers in New York, United States, and in backup storage there. Tools and their data stay as long as your workspace exists. When you delete a tool with purge, its data is removed at once and leaves the backups within 30 days. When a workspace closes, we remove its data 30 days later. Logs are kept for days, not months.

## Your rights

Wherever you live, you can see, export, correct or delete your data: `bh export` gives you a tool as one file, your account page shows what we hold, and an email to us does the rest, usually within a week. If you are in the European Union, United Kingdom, or California, you also have the rights those laws give you, and you can ask us to explain how we handle a specific case.

## Security

Everything travels over HTTPS. Passwords and keys are stored hashed; secrets you give a tool are encrypted at rest. Each tool runs in its own container with its own database. Access to our servers is by key only. If we ever learn of a breach that affects you, we tell you promptly.

## Children

The Service is for businesses and adults. We do not knowingly collect data from anyone under 18.

## Changes

If this policy changes in a way that matters, we email the address on your account before it takes effect.

## Contact

[support@example.com](mailto:support@example.com), or {OPERATOR}, the deployment operator’s jurisdiction.
"""
