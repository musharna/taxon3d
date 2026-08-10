"""Every third party the visitor's browser contacts must be disclosed on /privacy.

The privacy page said "We do not sell or share this data with third parties" — true of the vote
data, and incomplete. Loading any page made the browser fetch from four hosts we do not control,
which hands each of them an IP address and request headers. "We share nothing" and "nobody sees
you" are different claims and only the first was ever true.

Measured from a real browser session against the live site (not read off the templates, which
cannot see runtime asset hosts):

    fonts.googleapis.com                       stylesheet  (REMOVED — self-hosted since 2026-08-09)
    fonts.gstatic.com                          font        (REMOVED — self-hosted since 2026-08-09)
    static.cloudflareinsights.com              script
    <account>.r2.cloudflarestorage.com         image
    challenges.cloudflare.com                  script  (arena only, from _captcha.html)

The two Google hosts are gone: the typefaces are served from this origin, so no third party
sees a visitor merely for rendering text. `test_the_fonts_are_not_fetched_from_google` keeps
them gone — removing a party from the privacy page is only honest if re-adding it cannot pass
silently, and a stylesheet link would still render perfectly if someone pointed it back at a CDN.

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
    # The scanner must still find the real third parties that ARE embedded, or
    # test_every_auto_fetched_third_party_is_disclosed passes because it found nothing rather
    # than because everything it found was disclosed. This anchor moved off the Google Fonts
    # link when that link was deleted — a positive control has to point at something present.
    found = _external_hosts_in_templates()
    assert "challenges.cloudflare.com" in found, (
        f"the scanner no longer sees the verification widget embedded in _captcha.html; it "
        f"found {sorted(found)}. Until that is explained, the disclosure test above proves "
        "nothing."
    )


def test_the_fonts_are_not_fetched_from_google():
    """The typefaces are served from this origin, and must stay that way.

    Deleting a party from the privacy page is only honest if putting it back cannot pass
    quietly, and this one would: a `<link>` pointed at fonts.googleapis.com renders perfectly,
    so nothing about the page would look wrong. The failure is invisible by construction —
    every visitor's IP goes to a third party and only this assertion notices.
    """
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        # Strip comments first, exactly as the host scanner does. A Jinja comment explaining
        # why the fonts are self-hosted names both hosts and fetches neither — matching raw
        # text made this guard fail on the very state it exists to protect, which its own
        # negative control caught on the first run.
        text = re.sub(r"\{#.*?#\}", "", path.read_text(), flags=re.DOTALL)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        for host in ("fonts.googleapis.com", "fonts.gstatic.com"):
            if host in text:
                offenders.append(f"{path.name}: {host}")
    assert not offenders, (
        f"templates fetch fonts from Google again: {offenders}. The faces are self-hosted in "
        "app/static/fonts/ and declared in app/static/fonts.css — use those, or disclose the "
        "third party on /privacy and re-add it to DISCLOSED."
    )


def test_the_self_hosted_faces_exist_and_are_licensed():
    """A font sheet that references missing files degrades silently to a fallback face.

    The visible symptom would be "the site looks slightly off", which nobody files a bug for,
    so assert the referenced files are actually on disk. And because both families ship under
    the SIL OFL, which requires the licence to travel with the fonts, assert that too — this
    repo gates its own corpus on licensing and cannot be sloppier about its typefaces.
    """
    sheet = REPO / "app" / "static" / "fonts.css"
    assert sheet.exists(), "app/static/fonts.css is missing; base.html links it"
    css = sheet.read_text()
    referenced = re.findall(r"url\('(/static/fonts/[^']+)'\)", css)
    assert referenced, "fonts.css declares no @font-face src — nothing is self-hosted"
    missing = [r for r in referenced if not (REPO / "app" / r.removeprefix("/")).exists()]
    assert not missing, f"fonts.css references files that do not exist: {missing}"
    fonts_dir = REPO / "app" / "static" / "fonts"
    licences = list(fonts_dir.glob("LICENSE-*.txt"))
    assert len(licences) >= 2, (
        f"expected an OFL licence beside the fonts for each family, found {licences}"
    )
