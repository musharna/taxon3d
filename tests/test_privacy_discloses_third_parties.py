"""Every third party the visitor's browser contacts must be disclosed on /privacy.

The privacy page said "We do not sell or share this data with third parties" — true of the vote
data, and incomplete. Loading any page made the browser fetch from four hosts we do not control,
which hands each of them an IP address and request headers. "We share nothing" and "nobody sees
you" are different claims and only the first was ever true.

Measured from a real browser session against the live site (not read off the templates, which
cannot see runtime asset hosts):

    fonts.googleapis.com                       stylesheet
    fonts.gstatic.com                          font
    static.cloudflareinsights.com              script
    <account>.r2.cloudflarestorage.com         image
    challenges.cloudflare.com                  script  (arena only, from _captcha.html)

The mechanism this guards is drift: a new embed, CDN or widget added to a template is a new
party seeing every visitor, and nothing about adding it prompts anyone to touch the privacy
page. An unrecognized external host fails this test, which forces the disclosure decision to be
made rather than skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "app" / "templates"
PRIVACY = TEMPLATES / "privacy.html"

#: host substring -> the text that must appear on the privacy page for it to count as disclosed.
#: Keyed on the vendor a visitor would recognise, not the hostname, because that is what makes
#: the page useful to read.
DISCLOSED = {
    "fonts.googleapis.com": "fonts.googleapis.com",
    "fonts.gstatic.com": "fonts.gstatic.com",
    "challenges.cloudflare.com": "Cloudflare",
    "cloudflareinsights.com": "Cloudflare",
    "r2.cloudflarestorage.com": "Cloudflare",
    # Provider-gated and NOT loaded under the current config, which is precisely why it needed
    # disclosing: a live browser probe had already enumerated four third parties and missed this
    # one, because a measurement only sees the configuration it ran against. The template sees
    # every configuration the code can take. This test found it on its first run.
    "js.hcaptcha.com": "hCaptcha",
}

#: Hosts that appear in templates but are never contacted by a visitor's browser: links a person
#: may CLICK are not third parties who see them. Anything not here and not in DISCLOSED fails.
NOT_CONTACTED = (
    "github.com",
    "zenodo.org",
    "doi.org",
    "creativecommons.org",
    "lmarena.ai",
    "orcid.org",
    "wikipedia.org",
    "wikimedia.org",
    "inaturalist.org",
    "gbif.org",
    "schema.org",
    "w3.org",
)

#: Attributes whose URL the browser fetches automatically. `href` is deliberately excluded
#: EXCEPT for stylesheet links: an <a href> is a place the visitor may choose to go, which is
#: not the same as a party that sees them for loading the page.
AUTO_FETCH = re.compile(
    r'(?:src|data-src)="(https://[^"]+)"|<link[^>]+href="(https://[^"]+)"', re.IGNORECASE
)


def _external_hosts_in_templates() -> set[str]:
    hosts: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        text = re.sub(r"\{#.*?#\}", "", path.read_text(), flags=re.DOTALL)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        for a, b in AUTO_FETCH.findall(text):
            url = a or b
            host = url.split("/")[2]
            if not host.endswith("taxon3d.org"):
                hosts.add(host)
    return hosts


def test_every_auto_fetched_third_party_is_disclosed():
    page = PRIVACY.read_text()
    undisclosed = []
    unknown = []
    for host in sorted(_external_hosts_in_templates()):
        if any(n in host for n in NOT_CONTACTED):
            continue
        match = next((k for k in DISCLOSED if k in host), None)
        if match is None:
            unknown.append(host)
        elif DISCLOSED[match] not in page:
            undisclosed.append((host, DISCLOSED[match]))
    assert not unknown, (
        f"templates auto-fetch from hosts this test does not know about: {unknown}. Each one "
        "sees every visitor's IP. Add it to DISCLOSED and describe it on /privacy, or to "
        "NOT_CONTACTED if the browser never actually requests it."
    )
    assert not undisclosed, f"/privacy does not mention these third parties: {undisclosed}"


def test_the_runtime_only_hosts_are_disclosed():
    """Cloudflare's analytics beacon and the R2 asset host appear in NO template — the beacon is
    injected at the edge and asset URLs come from the database — so a template scan alone would
    report a clean sheet while two parties watched every visitor. Pin them by name.
    """
    page = PRIVACY.read_text()
    for phrase in ("Cloudflare", "3D models", "traffic statistics"):
        assert phrase in page, f"/privacy no longer discloses {phrase!r}"


def test_the_scanner_ignores_links_but_catches_embeds():
    """Negative control plus positive control, in one place.

    A guard that treated every `<a href>` as a third party would flag GitHub and Zenodo and get
    itself suppressed as noise; one that missed `<script src>` would pass a stylesheet with a
    tracker in it. Assert both directions on markup rather than trusting the regex by eye.
    """
    assert not AUTO_FETCH.findall('<a href="https://github.com/musharna/taxon3d">repo</a>')
    assert AUTO_FETCH.findall('<script src="https://tracker.example/t.js"></script>')
    assert AUTO_FETCH.findall('<link rel="stylesheet" href="https://fonts.googleapis.com/css2" />')
    # The real stylesheet include must be visible to the scanner, or the fonts assertion above
    # passes because nothing was found rather than because it was disclosed.
    assert "fonts.googleapis.com" in {h for h in _external_hosts_in_templates()}, (
        "the scanner no longer sees the Google Fonts stylesheet link in base.html"
    )
