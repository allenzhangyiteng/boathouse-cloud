# How do I put a static website online with Boat House?

Boat House can host an ordinary HTML/CSS/JavaScript website. Your agent publishes the files and gets an HTTPS URL. A static site counts as one of the five lightweight tools in the organization plan.

## Runnable example

After [installing the CLI](/docs/install-agent):

```sh
bh init company-site --template static
cd company-site
python3 -m http.server 8080 --bind 127.0.0.1
```

Open http://127.0.0.1:8080 and edit `index.html`. Stop with Ctrl+C, connect your account and add hosting credit, then publish:

```sh
bh whoami
bh deploy
```

Open the deployment URL. New deployments start private. If this is intended to be a public website, explicitly make it readable by anyone:

```sh
bh access company-site public
```

Check the URL in a signed-out browser. It should now open without login. To restrict it to organization members again:

```sh
bh access company-site members
```

## What a static site can and cannot do

The browser can download the published HTML, JavaScript and other deployed files. Never include credentials or confidential data. Static files cannot safely hold secret API keys, process private writes or independently verify a user for a backend. Use a server app for those features.

If a frontend framework produces a build folder, have the agent run its build and deploy the output folder containing `index.html`. Server rendering and API routes require a compatible server container; they are not ordinary static files.

## Update or use your own domain

Edit the project and run `bh deploy` again. [Connect a domain](/docs/connect-domain) if you want your own business address. Domain registration is separate from hosting and requires an approved price.
