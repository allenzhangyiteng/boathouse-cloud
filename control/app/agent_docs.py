"""Question-based HTML and Markdown guides, with a single maintained source."""
from pathlib import Path
from . import pages

GUIDES = {
    "install-agent": ("How do I install Boat House in Cursor, Codex, Windsurf or Claude Code?", "Install the open-source Boat House skill and CLI, then connect your account once."),
    "create-app": ("How do I create an app that is ready for Boat House?", "Generate a working team notebook or static site with bh init, preview locally and publish when ready."),
    "deploy-claude-code-app": ("How do I deploy a Claude Code app with team login?", "Put an AI-built app online with a stable HTTPS URL, team access and persistent data."),
    "share-with-team": ("How do I share my app and its data with my team?", "Invite viewers, editors and developers using their email addresses, without hosting-provider seats."),
    "publish-static-website": ("How do I put a static website online with Boat House?", "Create, preview and publish an HTML website; choose explicitly when it becomes public."),
    "add-team-login": ("How do I add team login to an existing app?", "Use Boat House gateway login and verify signed identities inside your application."),
    "connect-domain": ("How do I give my app a real domain name?", "Connect a domain you own or review a quote before purchasing through Boat House."),
}


def read(slug):
    if slug not in GUIDES:
        raise KeyError(slug)
    return (Path(__file__).parent / "agent_guides" / (slug + ".md")).read_text()


def index_markdown():
    return "# Start with a question\n\n" + "\n".join(f"- [{title}](/docs/{slug})" for slug, (title, _) in GUIDES.items()) + "\n\n"


def render(slug, platform):
    title, description = GUIDES[slug]
    content = '<p><a href="/docs">All docs</a> · <a href="/docs/' + slug + '.md">Read as Markdown</a></p>' + pages._md(read(slug))
    content += '<p><a href="/signup">Get started</a> · <a href="/security">Security and current limitations</a> · <a href="https://github.com/allenzhangyiteng/boathouse-skills">Open-source skill</a></p>'
    document = pages.page(title + " | Boat House", content,
                      nav='<a href="/">Boat House</a><a href="/docs">Docs</a><a class=btn href="/account">My apps</a>', wide=True,
                      description=description, canonical=f"https://{platform}/docs/{slug}")
    guide_style = """<style>
main.wide{max-width:850px;overflow-wrap:anywhere}
main.wide pre{max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 var(--mono);background:var(--band);border:1px solid var(--hair);border-radius:6px;padding:16px;margin:20px 0}
main.wide pre code{font:inherit;background:none;border:0;padding:0}
@media(max-width:640px){.top nav>a:first-child{display:none}}
</style>"""
    return document.replace("</head>", guide_style + "</head>", 1)
