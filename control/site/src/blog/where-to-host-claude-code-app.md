title: How to deploy an app built with Claude Code
seo_title: Deploy an App Built with Claude Code
slug: where-to-host-claude-code-app
description: Built an app with Claude Code? Put it online with Boat House, connect your agent once, and share a working link with your team.
keyword: deploy claude code app, host claude code app
date: 2026-09-08
updated: 2026-09-16
related: share-internal-tools, vibe-coded-app-hosting
category: Deployment

> Claude Code can build and prepare your app. A hosting service gives other people a working link to it.
> With Boat House, you create an account and paste one connection message into the agent conversation where you built your app.
> Your agent handles the deployment steps. Teammates open the app in a browser.

## The short answer

To deploy an app built with Claude Code, give your agent access to a hosting service and ask it to publish your project. Boat House combines app hosting, sign-in, a database, and sharing for small team tools. It is open source under Apache 2.0, with managed hosting available for [$10 per organization per month for up to five lightweight apps](/#pricing).

Imagine you have built a tutoring tracker. It works on your laptop, but a colleague cannot use your local preview link from their own computer. Deploying gives the tracker a stable online address. Sharing gives your colleague access to the app and the same lesson records.

The tutoring tracker in our [real-platform demo](/demo) uses fictional business data. It shows the kind of app this guide describes.

## 1. Create your account

Choose [Get started](/signup), enter your account details, and confirm your email. Your account is where you manage your apps and hosted balance. Creating an account does not require a card; running a hosted app requires a funded balance.

You do not need to start by choosing a server, copying database credentials, or configuring a separate sign-in service.

## 2. Connect the agent that has your app

On your account page, choose **Copy connection**. Paste that message into your Claude Code conversation. The agent installs the Boat House connection and checks that it can reach your account.

Use the coding agent that can access your project files and run development tools. A chat that only contains a description of your idea does not have the app's source. The [connection requirements](/docs) explain the supported environment.

The connection message grants access to your account's tools. Treat it as private; do not put it in a public repository, screenshot, or shared document.

## 3. Ask for the result you want

For example: **“Put this tutoring tracker online with Boat House. Keep it private and share it with my team.”**

Your agent inspects the project, prepares the deployment, and publishes it. A static website can be deployed from its files. A server app needs the supported container format; the agent can help prepare that. Apps with an existing database or external service may need an adaptation or data migration before they are ready.

The goal is one connection message and plain-English requests. It is not a promise that every app can be migrated without questions. Your agent may still need a business decision, an external service credential, or approval for a purchase.

## 4. Open it and try a real task

Use the link the agent returns. Sign in and complete the task the app was built for: add a lesson, save a note, or update an invoice. Reload the page to check that the change persists.

Then [invite a teammate](/blog/share-internal-tools) and have them open it with their own account. Check that the right people can see or edit the right information. Publishing the app and confirming its business logic are separate steps; both matter.

## Updating the app later

Return to your connected agent, ask for the change, and ask it to publish the update. Boat House provides version rollback and export tools. Your team keeps using the app's address.

Before you store sensitive information, read the [security page](/security). Boat House does not claim that every app built by an agent is secure or suitable for regulated data.

## Questions

### Do my teammates need Claude Code?
No. Viewers and editors use the app in their browser. Someone changing the software needs the source and suitable development access.

### Does the 90-second setup include building and deploying my app?
No. It refers to connecting your agent after email confirmation. Funding, app preparation, builds, and migrations take additional time.

### Can I use a custom domain?
Yes. Ask your agent to connect a domain or quote a new one. Review the price before approving a purchase. A custom domain is separate from app access permissions.

### Can I host Boat House myself?
Yes. The software is open source under Apache 2.0. Self-hosting means you manage the infrastructure, costs, updates, and backups. See the source link on the homepage.
