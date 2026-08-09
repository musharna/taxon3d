"""First-run onboarding, as the arena actually SERVES it.

Companion to `test_onboarding_in_flow.py`, which reads the template and stylesheet as files and
pins the layout invariant (nothing out of flow). This one goes through the app, so it also covers
the rendering path — a panel behind a Jinja condition that never evaluates true would satisfy the
file-level checks and ship an arena with no guidance at all.

Rewritten 2026-08-08 with the panel itself: it asserted a dismiss ✕ and a "Start voting" CTA,
which were affordances of the floating card. A <details> needs neither — the summary row is the
toggle, and collapsing no longer stands between the voter and the ballot. What survives is the
behaviour those assertions were really protecting: the guidance is reachable, it explains the
three things a voter has to do, and it is shown once rather than on every visit.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_onboarding_is_served_as_a_collapsed_disclosure():
    html = client.get("/arena").text
    assert 'id="onboard-banner"' in html
    assert "<details" in html and "</details>" in html
    assert "New here?" in html, "the collapsed summary row is missing its label"
    # The three steps and the key hints are the substance; losing them silently would leave a
    # disclosure that discloses nothing.
    assert "onboard-steps" in html
    assert "Inspect" in html and "Compare" in html and "Vote" in html
    assert "<kbd>" in html


def test_the_served_disclosure_ships_collapsed():
    """Closed is the safe default and must survive the render, not just the template.

    Open is the state that costs vertical space — the 276px inline card is what put the models
    below the fold in the first place — so if arena.js fails to run, or a crawler renders the
    page without JS, the panel must stay shut. Expanding it for a genuine first visit is JS's
    job, asserted below.
    """
    html = client.get("/arena").text
    tag = html[html.rindex("<", 0, html.index('id="onboard-banner"')) :]
    tag = tag[: tag.index(">") + 1]
    assert " open" not in tag, f"the disclosure is served expanded: {tag!r}"


def test_onboarding_js_opens_once_and_persists():
    ajs = client.get("/static/arena.js").text
    assert "bio3d_onboarded" in ajs, "the shown-once flag is gone; the panel would reopen forever"
    assert ".open = true" in ajs, "nothing expands the panel for a first-time visitor"
    # Recording on `toggle` rather than on a close button is what makes "shown once" hold for a
    # voter who reads the panel and navigates away without collapsing it.
    assert '"toggle"' in ajs, "the panel no longer records that the voter has met it"
