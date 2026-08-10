"""The homepage hero must not set a width floor that a phone viewport cannot honour.

Measured at 390x844 against a local instance: the homepage scrolled sideways by **4px**. Every
hero child sat at `left: 34px` (30px `.b3d-main` padding + 4px `.b3d-hero` padding) with a width
of exactly 360px, so its right edge landed at 394 in a 390px viewport.

The source was `.b3d-hero-viz-ring-wrap { width: 360px }`. A grid item defaults to
`min-width: auto`, so a track cannot shrink below its item's intrinsic contribution, and a
definite `width` IS that contribution. The `max-width: 100%` sitting beside it never rescued
anything, because that percentage resolves against a parent the item was itself sizing.

The fix expresses the cap as `max-width` and lets `aspect-ratio` hold the square, so there is no
fixed width left to overflow — rather than adding a counterweight to one. It also corrected a
second defect nobody had reported: at 820px the old rule produced a 205px-wide, 360px-tall
"circle", because `max-width: 100%` clamped the width while `height: 360px` stayed put.

4px is invisible in a screenshot and obvious under a thumb: the page rubber-bands horizontally
on every touch scroll. That is why this is asserted rather than eyeballed.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STYLE = REPO / "app" / "static" / "style.css"

FIXED_PX_WIDTH = re.compile(r"(?<!max-)(?<!min-)\bwidth\s*:\s*\d+px")


def _rules_for(css: str, selector: str) -> list[str]:
    """Every rule body declared for exactly this selector, in source order.

    A list, not a dict keyed by selector: the stylesheet redeclares selectors inside `@media`
    blocks, and keying by name lets a later redeclaration silently replace the base rule this
    guard exists to inspect. That exact bug hid a `position: fixed` from the onboarding guard.
    """
    bodies = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        clean = re.sub(r"/\*.*?\*/", " ", sel, flags=re.DOTALL)
        parts = [p.strip().splitlines()[-1].strip() for p in clean.split(",") if p.strip()]
        if selector in parts:
            bodies.append(body)
    return bodies


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.DOTALL)


def test_the_hero_rings_have_no_fixed_pixel_width():
    css = _strip_comments(STYLE.read_text())
    bodies = _rules_for(css, ".b3d-hero-viz-ring-wrap")
    assert bodies, "no .b3d-hero-viz-ring-wrap rule found — was the hero renamed?"
    offenders = [m.group(0) for b in bodies if (m := FIXED_PX_WIDTH.search(b))]
    assert not offenders, (
        f"the hero ring wrap declares a fixed pixel width again: {offenders}. "
        "As a grid item's descendant "
        "that becomes a floor the hero track cannot shrink below, and the homepage scrolls "
        "sideways on a phone. Cap it with max-width and let aspect-ratio hold the square."
    )


def test_the_hero_rings_are_still_capped_and_square():
    """Removing the fixed width must not have removed the design: 360px, and circular.

    Without this, deleting the rule entirely would satisfy the test above while letting the
    rings stretch to whatever the column happens to be.
    """
    css = _strip_comments(STYLE.read_text())
    joined = " ".join(_rules_for(css, ".b3d-hero-viz-ring-wrap"))
    assert re.search(r"max-width\s*:\s*360px", joined), (
        f"the hero rings lost their 360px cap: {joined!r}"
    )
    assert re.search(r"aspect-ratio\s*:\s*1", joined), (
        "the hero rings are capped but no longer forced square, so a narrow column renders them "
        f"as an ellipse: {joined!r}"
    )


def test_the_hero_viz_column_may_shrink():
    """`min-width: 0` on the grid item is the half of the fix that generalises.

    The ring wrap was one fixed-size descendant; any future one (a chart, an embed) would pin
    the track the same way. This keeps the column itself shrinkable.
    """
    css = _strip_comments(STYLE.read_text())
    joined = " ".join(_rules_for(css, ".b3d-hero-viz"))
    assert joined, "no .b3d-hero-viz rule found"
    assert re.search(r"min-width\s*:\s*0", joined), (
        "the hero viz grid item no longer sets `min-width: 0`, so it refuses to shrink below "
        f"the intrinsic width of whatever is inside it: {joined!r}"
    )


def test_the_checkers_fire_on_the_pre_fix_stylesheet():
    """Negative control, using the CSS that actually shipped the bug."""
    broken = ".b3d-hero-viz-ring-wrap {\n  width: 360px;\n  height: 360px;\n  max-width: 100%;\n}"
    assert FIXED_PX_WIDTH.search(_rules_for(broken, ".b3d-hero-viz-ring-wrap")[0]), (
        "the fixed-width detector does not fire on the exact rule that caused the overflow"
    )
    # ...and must NOT fire on the caps, or the guard would reject its own fix.
    fixed = ".b3d-hero-viz-ring-wrap {\n  width: 100%;\n  max-width: 360px;\n  aspect-ratio: 1;\n}"
    assert not FIXED_PX_WIDTH.search(_rules_for(fixed, ".b3d-hero-viz-ring-wrap")[0]), (
        "the detector treats `max-width: 360px` as a fixed width"
    )
    # A media-block redeclaration must be visible, not shadowed by the base rule.
    both = broken + "\n@media (max-width: 640px) {\n" + fixed + "\n}"
    assert len(_rules_for(both, ".b3d-hero-viz-ring-wrap")) == 2, (
        "the rule collector drops redeclarations, so a fixed width added inside @media would "
        "pass unnoticed"
    )
