"""Regression checks for crawlable, repeatable static publishing (no services required)."""
import importlib.util
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'control/site'
spec = importlib.util.spec_from_file_location('build_blog', SITE / 'src/build_blog.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class StaticSEO(unittest.TestCase):
    def test_comparison_table_is_one_table_with_body_rows(self):
        body, _, _ = build.render_body(['| Service | Cost |', '|:---|---:|', '| One | $10 |', '| Two | $20 |'])
        rendered = ''.join(body)
        self.assertEqual(rendered.count('<table>'), 1)
        self.assertEqual(rendered.count('<tr>'), 3)
        self.assertIn('<td>$20</td>', rendered)
        self.assertNotIn('---', rendered)

    def test_links_escape_attributes_and_reject_script_urls(self):
        self.assertNotIn('href=', build.inline('[bad](javascript:alert)'))
        link = build.inline('[quote](https://example.com/?q=" onmouseover="bad)')
        self.assertIn('&quot;', link)
        self.assertNotIn('href="https://example.com/?q="', link)
        self.assertNotIn('nofollow', build.inline('[source](https://example.com/)'))

    def test_article_chrome_and_metadata_do_not_leak_homepage_identity(self):
        page = (SITE / 'blog/share-internal-tools/index.html').read_text()
        self.assertEqual(page.count('<title>'), 1)
        self.assertEqual(len(re.findall(r'<h1[ >]', page)), 1)
        self.assertIn('/site/clean.css?v=', page)
        self.assertNotIn('google-site-verification', page)
        self.assertIn('href="#main"', page)
        self.assertIn('href="/#product"', page)
        self.assertIn('</footer>', page)
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)[1])
        self.assertEqual([g['@type'] for g in data['@graph']], ['Article', 'BreadcrumbList'])

    def test_sitemap_has_only_canonical_public_pages_and_truthful_dates(self):
        tree = ET.fromstring((SITE / 'sitemap.xml').read_text())
        ns = {'s':'http://www.sitemaps.org/schemas/sitemap/0.9'}
        entries = {e.find('s:loc',ns).text:e for e in tree}
        for path in ['/', '/demo','/security','/docs','/blog/share-internal-tools']:
            self.assertIn(build.HOST+path, entries)
        for path in ['/login','/signup','/account','/preview/home']:
            self.assertNotIn(build.HOST+path, entries)
        self.assertIsNone(entries[build.HOST+'/'].find('s:lastmod',ns))
        meta, _ = build.parse((SITE/'src/blog/where-to-host-claude-code-app.md').read_text())
        self.assertEqual(entries[build.HOST+'/blog/where-to-host-claude-code-app'].find('s:lastmod',ns).text, meta.get('updated') or meta['date'])

    def test_build_is_repeatable(self):
        paths = list((SITE/'blog').rglob('*.html')) + [SITE/'sitemap.xml',SITE/'robots.txt']
        before = {p:p.read_bytes() for p in paths}
        build.main()
        self.assertEqual(before, {p:p.read_bytes() for p in paths})

if __name__ == '__main__':
    unittest.main()
