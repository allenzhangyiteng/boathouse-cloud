# Your website

Edit `index.html`. Preview locally:

```sh
python3 -m http.server 8080 --bind 127.0.0.1
```

Open http://127.0.0.1:8080. After connecting your agent and adding hosting credit:

```sh
bh whoami
bh deploy
```

Deployments are private by default. For a public website, ask your agent to run `bh access YOUR_APP_SLUG public` after confirming that anyone should be able to read it. Replace `YOUR_APP_SLUG` with the slug in `boathouse.json`. Never put credentials, confidential files or private data in a static website; visitors can download the deployed files. Static sites cannot securely hold API keys or enforce data writes. Use the team starter for a server app.
