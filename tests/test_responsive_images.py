"""Images must not be able to overflow their container by default.

Measured on the live site before this rule existed, `/organisms/rosa` scrolled sideways by
**164px at 1440x900 and 960px at 390x844** — nearly three phone-screen widths. The culprit was
`organism.html`'s reference photograph: a bare `<img>` at its intrinsic 1280px inside a figure
with no CSS rule of its own.

The per-page fix would be `.b3d-org-ref img { max-width: 100% }`. That is a tripwire removal —
the MECHANISM is that the stylesheet has no baseline constraint on replaced elements, so every
`<img>` added without a bespoke rule overflows, and the sixteen organism pages are exactly the
pages an inbound link lands on. The rule belongs in the base layer.

Class- and id-scoped image rules keep winning on specificity (`.reference-panel .ref-img` is
0-2-0 against a bare `img` at 0-0-1), so images that are deliberately sized are unaffected.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STYLE = REPO / "app" / "static" / "style.css"


def _bare_img_rule(css: str) -> str | None:
    """The body of the global, unscoped `img { ... }` rule, if the stylesheet has one.

    Deliberately matches only a BARE `img` selector: `.card img { max-width: 100% }` constrains
    one component and leaves every other image unguarded, which is the state this file exists to
    prevent. `img,` in a selector list counts — a shared reset is still a global rule.
    """
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        # Strip comments, then split the WHOLE selector list on commas. Taking only the last
        # LINE — the obvious shortcut, and the one this file shipped first — reads
        # `img,\nvideo { ... }` as `video` and rejects a perfectly good reset. The negative
        # control below is what caught that.
        sel = re.sub(r"/\*.*?\*/", " ", selector, flags=re.DOTALL)
        parts = [p.strip().splitlines()[-1].strip() for p in sel.split(",") if p.strip()]
        if "img" in parts:
            return body
    return None


def test_images_are_constrained_to_their_container_by_default():
    body = _bare_img_rule(STYLE.read_text())
    assert body is not None, (
        "style.css has no global `img { ... }` rule, so any <img> added without a bespoke rule "
        "renders at its intrinsic width. That is what made /organisms/rosa scroll sideways by "
        "960px on a phone."
    )
    assert re.search(r"max-width\s*:\s*100%", body), (
        f"the global img rule does not cap width to its container: {body!r}"
    )
    assert re.search(r"height\s*:\s*auto", body), (
        "the global img rule caps width without `height: auto`, which squashes the aspect ratio "
        f"of every image it constrains: {body!r}"
    )


def test_the_organism_reference_photo_relies_on_that_default():
    """The page that was actually broken must be covered BY the global rule, not by a private
    one added alongside it — otherwise the global rule could be deleted and this page would look
    fine while every other image silently lost its guard.
    """
    html = (REPO / "app" / "templates" / "organism.html").read_text()
    assert "<img" in html, "organism.html no longer renders the reference photograph"
    css = STYLE.read_text()
    assert not re.search(r"\.b3d-org-ref\s+img\s*\{[^}]*max-width", css), (
        "a page-scoped max-width was added for .b3d-org-ref img. Rely on the global rule — a "
        "per-page fix leaves every other unstyled image able to overflow."
    )


def test_the_checker_fires_on_a_stylesheet_without_the_rule():
    """Negative control: a guard nobody has watched fail is not a guard.

    The second case is the one that matters — a component-scoped rule looks like a global
    constraint at a glance and is not one.
    """
    assert _bare_img_rule("body { margin: 0 }\n.card { padding: 1rem }") is None
    assert _bare_img_rule(".card img { max-width: 100%; }") is None, (
        "a component-scoped `.card img` rule was accepted as the global baseline"
    )
    assert _bare_img_rule("img { max-width: 100%; height: auto; }") is not None
    assert _bare_img_rule("img,\nvideo {\n  max-width: 100%;\n}") is not None, (
        "an `img, video` reset is a global rule and must be accepted"
    )
