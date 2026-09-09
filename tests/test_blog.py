"""The blog, sitemap and robots.txt are served on the platform host only; tool hosts tell crawlers to stay out."""
import os
import sys
import tempfile

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PUBLIC_IP="203.0.113.4", BH_PG_PASSWORD="x",
                  BH_OWNERS="owner@starter.test", BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-blog-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

PLAT = "platform.test"
P = {"host": PLAT}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver", follow_redirects=False) as cl:
        yield cl


def test_blog_index_and_a_post_are_served(client):
    r = client.get("/blog", headers=P)
    assert r.status_code == 200 and "<title>Blog · Boat House</title>" in r.text
    r = client.get("/blog/replit-deployment-cost", headers=P)
    assert r.status_code == 200 and 'rel="canonical" href="https://boathousecloud.com/blog/replit-deployment-cost"' in r.text
    assert "application/ld+json" in r.text


def test_unknown_or_odd_slugs_are_404(client):
    assert client.get("/blog/nope-not-here", headers=P).status_code == 404
    assert client.get("/blog/..%2Findex.html", headers=P).status_code == 404


def test_sitemap_and_robots_on_the_platform_host(client):
    r = client.get("/sitemap.xml", headers=P)
    assert r.status_code == 200 and "<urlset" in r.text and "/blog/replit-deployment-cost" in r.text
    r = client.get("/robots.txt", headers=P)
    assert r.status_code == 200 and "Sitemap: https://boathousecloud.com/sitemap.xml" in r.text


def test_tool_hosts_refuse_crawlers_and_have_no_blog(client):
    H = {"host": f"db.shared.{PLAT}"}
    r = client.get("/robots.txt", headers=H)
    assert r.status_code == 200 and "Disallow: /" in r.text
    assert client.get("/sitemap.xml", headers=H).status_code == 404
    assert client.get("/blog", headers=H).status_code == 404
