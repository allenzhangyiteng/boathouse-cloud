# How do I install Boat House in Cursor, Codex, Windsurf or Claude Code?

Install the skill, then tell your agent what you want to publish. The skill teaches the agent to run Boat House's `bh` command. You sign in once and paste **Copy connection** from your account page; the agent handles the technical setup. Installation itself does not create an account, deploy an app or charge a card.

## Cursor, Codex and Windsurf

Run this in your project, or ask your agent to run it:

```sh
npx skills add allenzhangyiteng/boathouse-skills
```

Choose your agent in the installer. To select all three explicitly:

```sh
npx skills add allenzhangyiteng/boathouse-skills --skill boathouse --agent cursor codex windsurf --yes
```

The current skills installer needs Node.js 22.20 or newer. The Boat House CLI needs Python 3.9 or newer. For all projects, add `--global`. Installing the skill gives the agent instructions; on first use it installs the CLI if needed.

## Claude Code

Add our public marketplace once, then install its plugin inside Claude Code:

```text
/plugin marketplace add allenzhangyiteng/boathouse-skills
/plugin install boathouse@boathouse
```

This is Boat House's own marketplace. An official-directory submission is separate and is not an endorsement. The plugin includes the skill and an explicit `/boathouse:setup` command. It does not run a startup hook or silently change your credentials. When the plugin loads, say “Put this online with Boat House.”

## Runnable first example

On macOS, Linux, or Windows with WSL, this installs the CLI without connecting an account, then creates a local starter:

```sh
curl -fsSL https://boathousecloud.com/install.sh -o /tmp/boathouse-install.sh
sh /tmp/boathouse-install.sh
~/.local/bin/boathouse init first-app --template team
cd first-app
python3 app.py --local
```

Open http://127.0.0.1:8080. Stop the preview with Ctrl+C. `boathouse` and `bh` refer to the same CLI. If another program already owns the `boathouse` filename, the installer preserves it; use `~/.local/bin/bh`.

## Connect when you are ready to publish

Create your account, confirm your email, and use [Copy connection](/account). Paste the whole message into the agent conversation containing your app. The agent verifies `bh whoami`, checks your hosting balance and publishes only when requested. Keep connection codes private and let the browser handle passwords and card details.

Hosting is $10 per organization per month for up to five lightweight tools, billed daily from prepaid credit. Domain purchases and approved extra storage are separate. [Create an app](/docs/create-app) or [deploy an existing app](/docs/deploy-claude-code-app).
