"""First-run guidance must live in the document flow, never floating over the ballot.

The onboarding card was an inline block costing 276px of the first viewport — the panel
explaining "drag to rotate" was pushing the rotatable models off-screen — so on 2026-08-08 it
became a `position: fixed` card pinned bottom-right. That traded a measurable bug for a worse
one: a fixed overlay's relationship to the controls underneath it is decided by viewport
arithmetic. Measured with `elementFromPoint` on the pick targets:

    1920x1080   0 blocked
    2560x1440   0 blocked
    1440x900    pick 1 BLOCKED by H2.onboard-title [inside .onboard-card]
    1366x768    pick 1 BLOCKED by SPAN [inside .onboard-card]

Going back to a pairwise default makes it worse still, because the bottom-right corner is where
the vote bar lives — the primary control, not a corner model.

Neither position is the fix, because both answer "where should the panel go" when the real
question is how much room first-run guidance deserves. A collapsed one-line disclosure costs
~28px instead of 276, stays in flow so no geometry can put it over a control, and leaves the
guidance available rather than imposed. This file pins the invariant that makes the class of bug
impossible: nothing in the onboarding subtree is taken out of flow.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARENA = REPO / "app" / "templates" / "arena.html"
STYLE = REPO / "app" / "static" / "style.css"

#: Declarations that take an element out of the document flow, so its overlap with anything else
#: is decided by coordinates rather than by layout.
OUT_OF_FLOW = re.compile(r"position\s*:\s*(fixed|absolute|sticky)", re.IGNORECASE)


def _onboard_rules(css: str) -> list[tuple[str, str]]:
    """Every rule whose selector mentions the onboarding subtree, as (selector, body) PAIRS.

    Rules nested in `@media` blocks are included: the 1366px and 1440px failures above were
    produced by exactly such a rule, so a checker that skipped them would miss the measured bug.

    A list rather than a dict, and that is load-bearing. The same selector is routinely declared
    twice — once at top level, once inside a media query — so keying by selector let the
    media-query body overwrite the base one. The first version of this file did exactly that and
    silently dropped the `position: fixed` declaration the checker exists to catch. The negative
    control below is what caught it, which is the whole argument for having one.
    """
    rules: list[tuple[str, str]] = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        sel = selector.strip().splitlines()[-1].strip()
        if "onboard" in sel.lower():
            rules.append((sel, body))
    return rules


def test_no_onboarding_rule_takes_it_out_of_flow():
    offenders = {
        sel: OUT_OF_FLOW.search(body).group(0)  # type: ignore[union-attr]
        for sel, body in _onboard_rules(STYLE.read_text())
        if OUT_OF_FLOW.search(body)
    }
    assert not offenders, (
        f"onboarding rules take the panel out of the document flow: {offenders}. An out-of-flow "
        "panel overlaps whatever the viewport arithmetic puts beneath it — which was measured "
        "covering the pick button at 1440x900 and 1366x768, and would now cover the vote bar. "
        "Keep first-run guidance in flow and small."
    )


#: The CSS as shipped between 2026-08-08 and this change. Kept verbatim so the checker above is
#: pinned to a rule that really was served — if a later refactor stops it firing here, the
#: overlay can come back unnoticed.
SHIPPED_OVERLAY_CSS = """
.onboard-card:not([hidden]) {
  position: fixed;
  z-index: 60;
  right: 1.25rem;
  bottom: 1.25rem;
  width: min(30rem, calc(100vw - 2.5rem));
}
@media (max-width: 720px) {
  .onboard-card:not([hidden]) {
    right: 0.75rem;
    left: 0.75rem;
  }
}
"""


def test_the_checker_fires_on_the_overlay_that_shipped():
    """The negative control: a guard nobody has watched fail is not a guard.

    `position: fixed` here is not hypothetical styling — it is the rule that produced the
    measured `elementFromPoint` blocks in this module's docstring.
    """
    rules = _onboard_rules(SHIPPED_OVERLAY_CSS)
    assert rules, "the rule extractor found no onboarding rules in the CSS that shipped"
    assert any(OUT_OF_FLOW.search(body) for _sel, body in rules), (
        "the checker no longer flags the fixed-position card that actually shipped; it has been "
        "narrowed into decoration."
    )


def test_onboarding_sits_inside_the_arena_brief():
    """In-flow is necessary but not sufficient — it also has to be inside the height budget.

    `.arena-brief` caps everything a voter reads before the models (see --arena-brief-max). An
    in-flow panel placed OUTSIDE that wrapper would be back to the original 276px bug: no
    overlap, but the models pushed below the fold again.
    """
    html = ARENA.read_text()
    brief = html.index('<div class="arena-brief">')
    onboard = html.index('id="onboard-banner"')
    assert onboard > brief, (
        "the onboarding panel is rendered before .arena-brief, so it is outside the height "
        "budget that keeps the models above the fold."
    )
    after_brief = html[brief:]
    depth_at_onboard = after_brief[: after_brief.index('id="onboard-banner"')]
    assert depth_at_onboard.count("<div") > depth_at_onboard.count("</div>"), (
        "the onboarding panel is after .arena-brief opens but not nested inside it."
    )


def test_onboarding_is_collapsed_by_default_and_keeps_its_js_managed_ids():
    """The disclosure must start closed for a returning voter, and must keep the two ids
    `arena.js` rewrites per ballot shape (`setBallotHelp`) — which the copy guard also exempts.
    Renaming them silently would leave a four-model voter reading two-model instructions.
    """
    html = ARENA.read_text()
    assert "<details" in html and 'id="onboard-banner"' in html, (
        "the onboarding panel is no longer a <details> disclosure"
    )
    marker = html.index('id="onboard-banner"')
    tag_start = html.rindex("<", 0, marker)
    open_tag = html[tag_start : html.index(">", marker) + 1]
    assert " open" not in open_tag, (
        "the disclosure ships expanded; first-visit expansion is arena.js's job so that a "
        "returning voter gets the collapsed one-line version."
    )
    for el_id in ("onboard-vote-step", "onboard-keys"):
        assert f'id="{el_id}"' in html, f"{el_id} disappeared; arena.js setBallotHelp targets it"
