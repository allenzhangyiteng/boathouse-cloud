# How do I share my app and its data with my team?

Tell your connected agent who should have access and what they should be able to do. For example: “Share the notebook with my co-founder as an editor.” If the agent cannot infer the email, it asks for that one missing detail. Teammates use their own login and the same live app and data.

Viewers read. Editors use the app's editing features. Admins can change source code, secrets, sharing and deployments, including deleting the app. Admin is appropriate for trusted developers, not everyone who edits a spreadsheet-like record. There is no Boat House per-person hosting seat charge.

## Runnable example

Create and preview a notebook first:

```sh
bh init shared-notebook --template team
cd shared-notebook
python3 app.py --local
```

Open http://127.0.0.1:8080. Stop with Ctrl+C. After connecting and funding your Boat House account, publish:

```sh
bh whoami
bh deploy
```

Set `TEAMMATE_EMAIL` to the real email you intend to invite, then run:

```sh
bh share shared-notebook "${TEAMMATE_EMAIL:?Set the email of the person you intend to invite}" --tier editor
```

That last command grants access and sends an invitation. Do not use an invented or sample address. The owner can instead ask the agent to run the command with a confirmed email.

## Confirm it works

The invited person opens their email, verifies the invitation and signs in. They should see the same notes and be able to add one. Give a different test account Viewer access and confirm that it cannot add notes. Removing access with `bh unshare` must prevent subsequent access. The app must enforce record-level restrictions if some notes should be hidden from some team members.

## Let another developer use their agent

Grant Admin only when the person should control the software itself. They connect their own agent, then run `bh pull organization/shared-notebook`, edit the downloaded project and run `bh deploy`. They use the same hosted app and release history. They do not need to receive your API key or password, and they can use a different supported coding agent.

This is source checkout and versioned publishing, not simultaneous character-by-character code editing. Boat House detects stale updates so one developer does not silently replace another's newer release.
