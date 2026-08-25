from fastapi.testclient import TestClient
from app import config
from app.main import app


def test_dataset_page_no_release(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path / "releases")
    r = TestClient(app).get("/dataset")
    assert r.status_code == 200
    assert "no release" in r.text.lower()


def test_dataset_page_lists_release(monkeypatch, tmp_path):
    rel = tmp_path / "releases" / "2026.07-v1"
    rel.mkdir(parents=True)
    (rel / "VERSION").write_text("2026.07-v1\nsha256:abc\n")
    (rel / "DATASHEET.md").write_text("# Datasheet 2026.07-v1\n")
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path / "releases")
    r = TestClient(app).get("/dataset")
    assert r.status_code == 200
    assert "2026.07-v1" in r.text


def _dataset_ld(client):
    """Pull the schema.org Dataset markup out of the rendered page."""
    import json
    import re

    r = client.get("/dataset")
    assert r.status_code == 200
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
    assert m, "no JSON-LD block on /dataset"
    return json.loads(m.group(1)), r.text


def test_jsonld_offers_the_hf_corpus_as_a_real_download(monkeypatch, tmp_path):
    """The one `distribution` entry that names a file someone can actually fetch.

    This markup is the only thing Google Dataset Search reads (`_dataset_jsonld`'s own docstring
    says so), and every release entry sets `contentUrl` to `/dataset` — the page describing the
    data, not the data. A crawler following it lands back where it started, so the Dataset record
    has no retrievable distribution at all. The Hugging Face repo is the first one that does.
    """
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path / "releases")
    monkeypatch.setattr(config, "HF_DATASET_REPO", "musharna/taxon3d-corpus-v1")
    ld, html = _dataset_ld(TestClient(app))

    urls = [d.get("contentUrl") for d in ld.get("distribution", [])]
    assert "https://huggingface.co/datasets/musharna/taxon3d-corpus-v1" in urls, (
        f"HF corpus missing from distribution: {urls}"
    )
    # And a human on the page can reach it too, not only a crawler.
    assert "huggingface.co/datasets/musharna/taxon3d-corpus-v1" in html


def test_jsonld_omits_the_corpus_when_none_is_published(monkeypatch, tmp_path):
    """NEGATIVE CONTROL, and the rule `_dataset_jsonld` already states for releases.

    "Markup promising a download that isn't there is worse than no markup" — so an instance with
    no published corpus must assert none. Without this, the URL could be hardcoded into the
    template and the test above would still pass, while every fork and local dev instance
    advertised someone else's dataset as its own distribution.
    """
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path / "releases")
    monkeypatch.setattr(config, "HF_DATASET_REPO", "")
    ld, html = _dataset_ld(TestClient(app))

    urls = [d.get("contentUrl") for d in ld.get("distribution", [])]
    assert not any("huggingface.co" in (u or "") for u in urls), (
        f"claimed an HF distribution with no corpus configured: {urls}"
    )
    assert "huggingface.co/datasets" not in html
