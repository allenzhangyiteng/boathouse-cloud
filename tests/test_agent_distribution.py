"""Public agent docs, working routes and installer compatibility."""
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

os.environ.setdefault("BH_DOMAIN", "platform.test")
os.environ.setdefault("BH_PG_PASSWORD", "synthetic")
os.environ.setdefault("BH_STATE_DIR", tempfile.mkdtemp(prefix="bh-distribution-tests-"))
os.environ["BH_METER"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "control"))

from fastapi.testclient import TestClient
from app import agent_docs, config, front, main


def test_question_guides_markdown_full_text_sitemap_and_host_boundary():
    client = TestClient(main.app, base_url="https://" + config.PLATFORM_DOMAIN)
    full = client.get("/llms-full.txt")
    assert full.status_code == 200 and full.headers["content-type"].startswith("text/plain")
    sitemap = ET.fromstring(client.get("/sitemap.xml").text)
    urls = [n.text for n in sitemap.iter() if n.tag.endswith("}loc")]
    index = client.get("/docs").text
    for slug, (title, _) in agent_docs.GUIDES.items():
        r = client.get("/docs/" + slug)
        md = client.get("/docs/" + slug + ".md")
        assert r.status_code == md.status_code == 200
        assert len(re.findall(r"<h1[ >]", r.text)) == 1
        assert 'rel="canonical"' in r.text and "/docs/" + slug in index
        assert md.text == agent_docs.read(slug) and md.text in full.text
        assert md.text.startswith("# How do I ") and "```sh" in md.text
        assert any(url.endswith("/docs/" + slug) for url in urls)
        assert client.get("/docs/" + slug, headers={"Host":"api." + config.PLATFORM_DOMAIN}).status_code == 404
    assert client.get("/docs/not-a-guide").status_code == 404
    assert client.get("/llms-full.txt", headers={"Host":"api." + config.PLATFORM_DOMAIN}).status_code == 404
    assert client.get("/boathouse").content == client.get("/bh").content


def test_registry_proof_only_serves_public_file_and_only_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(front, "SITE", tmp_path)
    client = TestClient(main.app, base_url="https://" + config.PLATFORM_DOMAIN)
    path = "/.well-known/mcp-registry-auth"
    assert client.get(path).status_code == 404
    proof = "v=MCPv1; k=ed25519; p=public-test-proof\n"
    (tmp_path / "mcp-registry-auth.txt").write_text(proof)
    assert client.get(path).text == proof
    assert client.get(path, headers={"Host":"api." + config.PLATFORM_DOMAIN}).status_code == 404
