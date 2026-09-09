#!/usr/bin/env python3
"""Build the Boat House blog from control/site/src/blog/*.md into control/site/blog/<slug>/index.html,
plus blog/index.html, sitemap.xml and robots.txt. Header and footer come from the marketing page so
every article looks like the site. Run after build.py: python3 control/site/src/build_blog.py

Article front matter (first lines, `key: value`, blank line ends it):
  title, slug, description, keyword, date (YYYY-MM-DD), category, updated (optional), faq (optional, in body)
Body is Markdown-lite: #, ##, ### headings, paragraphs, - bullets, 1. lists, **bold**, `code`, [text](url),
> takeaway lines at the top become the key-takeaways box, a `## Questions` section becomes the FAQ with schema.
"""
import datetime as dt, html, json, pathlib, re

here = pathlib.Path(__file__).parent
site = here.parent
SRC = here / "blog"
OUT = site / "blog"
HOST = "https://boathousecloud.com"
AUTHOR = {"name": "Boat House contributors", "role": "Founder, Boat House"}

index_html = (site / "index.html").read_text()
head_top = index_html[: index_html.index("<title>")]
header = index_html[index_html.index('<a class="skip"'): index_html.index("<main")]
footer = index_html[index_html.index("<footer"): index_html.index("<script>")]
css_link = re.search(r'<link rel="stylesheet" href="/site/site.css[^"]*">', index_html).group(0)
fonts = re.search(r'<link rel="preconnect".*?display=swap">', index_html, re.S).group(0)
blog_css = f'<link rel="stylesheet" href="/site/blog.css?v={__import__("hashlib").md5((site/"blog.css").read_bytes()).hexdigest()[:8]}">'
# the marketing nav links to page sections; on an article they must point back home
header = header.replace('href="#', 'href="/#')
footer = footer.replace('href="#', 'href="/#')


def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    s = re.sub(r"\[(.+?)\]\((.+?)\)", lambda m: f'<a href="{m.group(2)}"{" rel=nofollow noopener" if m.group(2).startswith("http") and HOST not in m.group(2) else ""}>{m.group(1)}</a>', s)
    return s


def parse(text):
    meta, body = {}, []
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].strip():
        k, _, v = lines[i].partition(":")
        meta[k.strip()] = v.strip()
        i += 1
    body = lines[i + 1:]
    return meta, body


def render_body(lines):
    out, takeaways, faq, para, lst = [], [], [], [], None
    in_faq = False
    q = None

    def flush_para():
        nonlocal para
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para = []

    def flush_list():
        nonlocal lst
        if lst:
            tag, items = lst
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            lst = None

    for raw in lines + [""]:
        line = raw.rstrip()
        if line.startswith("> "):
            takeaways.append(line[2:]); continue
        if line.startswith("## "):
            flush_para(); flush_list()
            if q: faq.append((q, " ".join(a_lines))); q = None
            if line[3:].strip().lower() == "questions":
                in_faq = True; continue
            in_faq = False
            hid = re.sub(r"[^a-z0-9]+", "-", line[3:].lower()).strip("-")
            out.append(f'<h2 id="{hid}">{inline(line[3:])}</h2>'); continue
        if in_faq:
            if line.startswith("### "):
                if q: faq.append((q, " ".join(a_lines)))
                q, a_lines = line[4:], []
            elif line.strip() and q:
                a_lines.append(line.strip())
            continue
        if line.startswith("### "):
            flush_para(); flush_list(); out.append(f"<h3>{inline(line[4:])}</h3>"); continue
        m = re.match(r"^(-|\d+\.) (.+)", line)
        if m:
            flush_para()
            tag = "ul" if m.group(1) == "-" else "ol"
            if lst and lst[0] != tag: flush_list()
            lst = lst or (tag, []); lst[1].append(m.group(2)); continue
        if line.startswith("| "):
            flush_para(); flush_list()
            rows = [line]; out.append(("table", rows)); continue
        if not line.strip():
            flush_para(); flush_list(); continue
        if out and isinstance(out[-1], tuple) and out[-1][0] == "table" and line.startswith("|"):
            out[-1][1].append(line); continue
        para.append(line.strip())
    if q: faq.append((q, " ".join(a_lines)))
    # tables
    final = []
    for x in out:
        if isinstance(x, tuple):
            rows = [r.strip().strip("|").split("|") for r in x[1] if not re.match(r"^\|?\s*-+", r)]
            head, body = rows[0], rows[1:]
            final.append('<div class="tbl"><table><thead><tr>' + "".join(f"<th>{inline(c.strip())}</th>" for c in head) + "</tr></thead><tbody>"
                         + "".join("<tr>" + "".join(f"<td>{inline(c.strip())}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table></div>")
        else:
            final.append(x)
    return final, takeaways, faq


def words(lines):
    return len(" ".join(lines).split())


def article_page(meta, body_lines, others):
    body, takeaways, faq = render_body(body_lines)
    n = words(body_lines)
    mins = max(3, round(n / 220))
    date = dt.date.fromisoformat(meta["date"])
    updated = dt.date.fromisoformat(meta["updated"]) if meta.get("updated") else date
    url = f"{HOST}/blog/{meta['slug']}"
    tk = ""
    if takeaways:
        tk = '<aside class="takeaways"><h2>The short version</h2><ul>' + "".join(f"<li>{inline(t)}</li>" for t in takeaways) + "</ul></aside>"
    faq_html = ""
    if faq:
        faq_html = '<section class="faq-block"><h2 id="questions">Questions people ask</h2><div class="faq">' + "".join(
            f"<details><summary>{inline(q)}</summary><p>{inline(a)}</p></details>" for q, a in faq) + "</div></section>"
    related = [o for o in others if o["slug"] != meta["slug"]][:3]
    rel_html = ""
    if related:
        rel_html = '<section class="related"><h2>Read next</h2><ul>' + "".join(
            f'<li><a href="/blog/{o["slug"]}">{html.escape(o["title"])}</a><small>{html.escape(o["description"])}</small></li>' for o in related) + "</ul></section>"
    schema = {
        "@context": "https://schema.org", "@graph": [
            {"@type": "Article", "headline": meta["title"], "description": meta["description"], "datePublished": date.isoformat(),
             "dateModified": updated.isoformat(), "author": {"@type": "Person", "name": AUTHOR["name"]},
             "publisher": {"@type": "Organization", "name": "Boat House", "url": HOST, "logo": {"@type": "ImageObject", "url": f"{HOST}/site/brand/boat-house-app-icon-ocean-1024.png"}},
             "mainEntityOfPage": url, "wordCount": n, "keywords": meta.get("keyword", "")},
            {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Boat House", "item": HOST + "/"},
                {"@type": "ListItem", "position": 2, "name": "Blog", "item": HOST + "/blog"},
                {"@type": "ListItem", "position": 3, "name": meta["title"], "item": url}]},
        ]}
    if faq:
        schema["@graph"].append({"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]})
    head = (f"{head_top}<title>{html.escape(meta['title'])} · Boat House</title>\n"
            f'<meta name="description" content="{html.escape(meta["description"])}">\n'
            f'<link rel="canonical" href="{url}">\n'
            f'<meta property="og:title" content="{html.escape(meta["title"])}">\n'
            f'<meta property="og:description" content="{html.escape(meta["description"])}">\n'
            f'<meta property="og:type" content="article">\n<meta property="og:url" content="{url}">\n'
            f'<meta property="og:image" content="{HOST}/site/brand/boat-house-app-icon-ocean-1024.png">\n'
            f'<meta property="article:published_time" content="{date.isoformat()}">\n'
            f'<meta name="twitter:card" content="summary">\n'
            f'<meta name="theme-color" content="#ffffff">\n<link rel="icon" href="/site/brand/favicon.svg" type="image/svg+xml">\n'
            f"{fonts}\n{css_link}\n{blog_css}\n"
            f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>\n</head>\n<body>\n')
    main = (f'<main id="main" tabindex="-1" class="post"><article class="wrap"><div class="col">'
            f'<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Boat House</a><span>/</span><a href="/blog">Blog</a><span>/</span><span>{html.escape(meta.get("category", "Guide"))}</span></nav>'
            f'<h1>{inline(meta["title"])}</h1><p class="lead">{inline(meta["description"])}</p>'
            f'<p class="byline"><span>{AUTHOR["name"]}, {AUTHOR["role"]}</span><span>{date.strftime("%B %-d, %Y")}</span><span>{mins} min read</span></p>'
            f'{tk}<div class="prose">{"".join(body)}</div>{faq_html}'
            f'<aside class="cta"><h2>Put your tool online, with logins, today.</h2><p>Boat House is the Google Doc for small software: your agent deploys it, gives it a login and a database, and shares it by email. $10 a month a tool, no card to sign up. Connect your agent in about 90 seconds after email confirmation; funding and app builds take extra time.</p><a class="btn btn-p chev" href="/signup">90-second agent setup</a> <a class="more" href="/docs">Read the docs</a></aside>'
            f'{rel_html}</div></article></main>')
    return head + header + main + footer + "</body>\n</html>\n", n


def index_page(posts):
    items = "".join(
        f'<li><a href="/blog/{p["slug"]}"><span class="cat">{html.escape(p.get("category", "Guide"))}</span><h2>{html.escape(p["title"])}</h2>'
        f'<p>{html.escape(p["description"])}</p><small>{dt.date.fromisoformat(p["date"]).strftime("%B %-d, %Y")} · {p["mins"]} min</small></a></li>'
        for p in posts)
    head = (f"{head_top}<title>Blog · Boat House</title>\n"
            f'<meta name="description" content="Plain answers about hosting the tools you build with Claude Code, Codex, Lovable, Replit and the rest: logins, domains, databases, costs.">\n'
            f'<link rel="canonical" href="{HOST}/blog">\n<meta property="og:title" content="The Boat House blog">\n'
            f'<meta property="og:description" content="Plain answers about hosting the tools you build with AI: logins, domains, databases, costs.">\n'
            f'<meta property="og:type" content="website">\n<meta property="og:url" content="{HOST}/blog">\n'
            f'<meta property="og:image" content="{HOST}/site/brand/boat-house-app-icon-ocean-1024.png">\n'
            f'<meta name="theme-color" content="#ffffff">\n<link rel="icon" href="/site/brand/favicon.svg" type="image/svg+xml">\n'
            f"{fonts}\n{css_link}\n{blog_css}\n</head>\n<body>\n")
    main = (f'<main id="main" tabindex="-1" class="post"><div class="wrap bloglist"><div class="col"><h1>Blog</h1>'
            f'<p class="lead">Plain answers about hosting the tools you build with AI: logins, domains, databases, what things cost.</p>'
            f'<ul class="posts">{items}</ul></div></div></main>')
    return head + header + main + footer + "</body>\n</html>\n"


def main():
    posts = []
    for f in sorted(SRC.glob("*.md")):
        meta, body = parse(f.read_text())
        meta["mins"] = max(3, round(words(body) / 220))
        posts.append({**meta, "_body": body, "_file": f})
    posts.sort(key=lambda p: p["date"], reverse=True)
    OUT.mkdir(exist_ok=True)
    for p in posts:
        page, n = article_page(p, p["_body"], posts)
        d = OUT / p["slug"]; d.mkdir(exist_ok=True)
        (d / "index.html").write_text(page)
        print(f"blog/{p['slug']}  {n} words")
    (OUT / "index.html").write_text(index_page(posts))
    urls = [(HOST + "/", dt.date.today().isoformat(), "weekly", "1.0"), (HOST + "/docs", dt.date.today().isoformat(), "weekly", "0.8"),
            (HOST + "/blog", max(p["date"] for p in posts) if posts else dt.date.today().isoformat(), "daily", "0.8")]
    urls += [(f"{HOST}/blog/{p['slug']}", p.get("updated") or p["date"], "monthly", "0.7") for p in posts]
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(
        f"  <url><loc>{u}</loc><lastmod>{m}</lastmod><changefreq>{c}</changefreq><priority>{pr}</priority></url>\n" for u, m, c, pr in urls) + "</urlset>\n"
    (site / "sitemap.xml").write_text(sm)
    (site / "robots.txt").write_text(f"User-agent: *\nAllow: /\nDisallow: /account\nDisallow: /welcome\nDisallow: /requests/\nSitemap: {HOST}/sitemap.xml\n")
    print(f"blog index, sitemap ({len(urls)} urls), robots.txt written")


if __name__ == "__main__":
    main()
