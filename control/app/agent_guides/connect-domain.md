# How do I give my app a real domain name?

Every Boat House app already has an HTTPS address. A custom domain is optional. Your agent can connect a domain you own, or check availability and show a purchase quote. It should establish which domain you mean and what you approve before spending money or changing live DNS.

## Runnable example without buying anything

Create a small website locally:

```sh
bh init domain-demo --template static
cd domain-demo
python3 -m http.server 8080 --bind 127.0.0.1
```

Open http://127.0.0.1:8080, then stop with Ctrl+C. After connecting your account, these commands inspect your account and existing domains without purchasing or changing DNS:

```sh
bh whoami
bh domain ls
bh domain renewals
```

Set `DOMAIN` to the domain you want to check, then:

```sh
bh domain check "${DOMAIN:?Set the exact domain to check}"
```

Checking does not reserve or purchase a domain. Publish with `bh deploy` after adding hosting credit; keep the free URL while reviewing the custom-domain plan.

## If you already own the domain

Tell the agent where it is registered and which app should answer there. The agent checks existing DNS before applying changes. For a registrar-managed domain connected to Boat House, `bh domain attach DOMAIN --to APP_SLUG` attaches it and points the app. Other registrars can require access to their DNS settings. Do not overwrite unrelated email records or another production website.

## If you want to buy a domain

Ask for the exact domain. `bh domain buy DOMAIN` first obtains a quote; it does not purchase without confirmation. The agent should state the total, registration duration and renewal price, then use the same quote ID and your maximum approved cost when confirming. Retry an uncertain purchase with that same operation; do not create a fresh order.

Domain purchases use prepaid Boat House credit. Boat House pays the registrar from its own registrar balance. The quoted price includes the registrar cost plus Boat House's $2-per-year fee; it is separate from the organization hosting plan. Availability and registrar credit can change before confirmation.

## Renewals

The owner controls auto-renewal and the maximum annual price on the account's Domains page or through `bh domain renewal`. Boat House sends advance notices and uses prepaid credit only within the approved limit. Insufficient customer or registrar credit can prevent renewal. Detaching a domain from an app does not cancel its renewal policy; review that setting separately.
