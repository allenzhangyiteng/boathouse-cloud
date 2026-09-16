title: How to share an internal tool with your team
seo_title: Share Internal Tools with Your Team
slug: share-internal-tools
description: Share an internal app and its data with named logins. Give teammates Viewer, Editor, or Admin access through your connected coding agent.
keyword: share internal tools, share app with team
date: 2026-09-11
updated: 2026-09-16
related: where-to-host-claude-code-app, vibe-coded-app-hosting
category: Sharing

> Sharing software should feel as simple as sharing a document: choose the people and the access they need.
> The app and its data stay in one place. Teammates open it in their browsers.
> Viewers and editors do not need a coding agent or a paid developer seat to use a Boat House app.

## The short answer

To share an internal tool, put it online, keep it private, and give each teammate access through their own account. With Boat House, your connected coding agent can deploy the app and invite the people you name. You can ask in plain English.

Suppose you built a project tracker for a small agency. The owner needs to change the software, project managers need to update tasks, and a colleague only needs to read progress. Sending everyone a copy of the files would create separate versions. Sharing one hosted app keeps the work in the same place.

This is an illustrative example. The [demo](/demo) shows a real Boat House platform walkthrough using fictional tutoring data.

## Start with one live app

If your tool only runs in a local preview, first [connect your agent and deploy it](/blog/where-to-host-claude-code-app). Open the published link and test a normal task. For an internal tool, keep the app private instead of making it publicly accessible.

Then tell your agent who should use it. For example: **“Share the project tracker with Taylor as an editor and Morgan as a viewer.”** Give the agent their email addresses when it asks. You choose the recipients; the agent handles the sharing steps.

Start with one teammate and check what they can see and change. Once that works, invite the rest of your team.

## Choose the right access

| Access | Intended use | Who needs it |
|---|---|---|
| Viewer | Open and read the app | Someone checking information |
| Editor | Use the app and update permitted data | Someone doing day-to-day work |
| Admin | Manage the tool and publish software changes | A trusted developer or maintainer |

Boat House controls who can open the app and passes the user's role to it. The application must use that role correctly for its own read and write operations. Ask your agent to check this, especially if you are moving an app that did not previously support different roles.

Test the viewer experience with sample data before relying on it. A label that says “Viewer” is not a substitute for enforcing permissions inside the app.

## Your teammate opens the invitation

A teammate follows the access link and signs in or completes the account setup. They then use the app in a browser. They do not need your Claude Code session, your connection code, or your hosting credentials.

Do not forward your own account password or agent connection message as a shortcut. Each person should have the access intended for them.

## Let another developer improve the software

For someone who will change the app itself, give appropriate Admin access and have them connect their own coding agent. Their agent can work with the source and publish updates. This is a different responsibility from editing a record in the app.

Ask the developer to test the change and preserve existing data before publishing. Use the [deployment and rollback documentation](/docs) when planning a release.

## Change access when the team changes

Ask your agent to remove a person who no longer needs the tool or change their role when their responsibilities change. Check that access has actually changed by testing the relevant account.

Boat House's hosted price is $10 per organization per month for up to five lightweight tools, with [no per-person seat charge](/#pricing). The software is open source if you prefer to run it yourself. Review the [security page](/security) for the platform's controls, shared responsibilities, and limitations.

## Questions

### Is sharing a tool the same as making it public?
No. A private app requires access. A public app can be opened without that private access check. Use private sharing for internal business tools.

### Can teammates edit the data without changing the software?
Yes. Editor access is intended for using the app and updating data it permits them to change. Changing the software or publishing a deployment is an Admin responsibility.

### Will we all see the same data?
A correctly configured app uses its shared hosted database. Check that your app saves records there rather than only in one person's browser or a temporary file.

### Can I take the app and data elsewhere?
Boat House provides export tools, and the platform is open source under Apache 2.0. Review the export format and your external dependencies before migrating.
