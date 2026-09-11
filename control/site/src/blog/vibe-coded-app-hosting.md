title: Hosting an AI-built app: what your team actually needs
seo_title: AI-Built App Hosting: A Practical Guide
slug: vibe-coded-app-hosting
description: A practical checklist for hosting an AI-built app: a stable URL, team sign-in, shared data, backups, and a way to publish updates.
keyword: ai app hosting, vibe coded app hosting
date: 2026-09-08
updated: 2026-09-11
related: share-internal-tools, where-to-host-claude-code-app
category: Hosting

> A working preview is the beginning. A shared app also needs a stable address, access controls, and data that lasts.
> Choose hosting around what your app does and who uses it.
> Boat House brings hosting, sign-in, a database, and sharing together for small team apps.

## The short answer

Hosting an AI-built app means running it somewhere your intended users can reach. For a public brochure site, serving the website files may be enough. For a team tool that stores customer notes, invoices, or lesson records, you also need a database, named access, and a way to maintain the app.

The tool that generated the code does not determine all of these choices. Look at the app itself: does it run a server, save information, call another service, or accept uploads? Ask your agent to explain those requirements before choosing where to host it.

## What belongs in the hosting decision

| Need | What to check | Why it matters |
|---|---|---|
| A stable address | The link works outside your local preview | Teammates can open the app from their own devices |
| Sign-in and access | People use their own accounts, with appropriate roles | You can grant and remove access for one person |
| Shared data | Records and files survive an app restart | Everyone sees the same saved work |
| Recovery | Backups, version history, and an export path | You have options when something goes wrong |
| Maintenance | Someone can publish updates and check errors | The app remains useful after its first launch |

A custom domain changes the address people see. It does not create logins or protect data by itself. Likewise, a login screen does not prove the app correctly restricts every operation. The app must enforce its permissions.

## Three reasonable ways to put it online

**Use your app builder's hosting** when its supported languages, access model, and costs fit your needs. Keeping development and hosting together can be convenient. Check whether collaborators need paid accounts and whether your data is easy to export.

**Assemble hosting, a database, and authentication services** when you or your developer want control over each part. Your agent can help configure those services, but someone still needs to own the accounts, connections, and ongoing maintenance.

**Use Boat House** when you want those core pieces together for a small shared app. Connect your coding agent, ask it to deploy, and share access with your team. Read the [Claude Code deployment walkthrough](/blog/where-to-host-claude-code-app) for the account-to-live-link steps; Codex and Cursor can also use the connection.

These choices are not interchangeable for every workload. Check the supported deployment format and your app's resource needs before moving an existing service.

## What Boat House includes

Managed hosting costs [$10 per running tool per month](/#pricing), billed daily from a prepaid balance. It includes a database, sign-in, nightly backups, and 1 GB of storage, with no per-person seat charge. Extra storage and domain purchases have separate prices shown on the pricing section.

Your agent handles the deployment commands. A viewer or editor does not need to install a development tool to use the app. A trusted developer can receive Admin access to maintain the software.

Boat House is also [open source](/#open-source). Running it yourself has no software license fee, but you take responsibility for the infrastructure and its costs.

## Check it before inviting everyone

Open the published app on a second device. Create a record, reload, and check that the record is still there. Test with a viewer and an editor to verify the app's permissions. Confirm what happens when you remove access.

Use sample data first. Then review the [security controls and current limitations](/security) against the information you intend to store. For a business-critical app, make sure someone knows how to recover the data and handle an outage.

## Questions

### Is a preview link enough for my business?
It depends on what the preview service promises. Check whether it stays available, saves data reliably, and supports your intended access controls before relying on it.

### Do I need to learn deployment commands?
With Boat House, you paste the connection message into a supported coding agent. The agent runs the technical steps. You provide the app and decisions about access, costs, and any external services.

### Can I move an existing app?
Often, but the agent may need to adapt its deployment format and migrate the data. Check external dependencies and plan the move before replacing the old service.
