"""Visitor-facing copy must describe the ballot the arena actually serves by default.

The durable rule is that one: copy must not contradict the default ballot. The specific
prohibition has now inverted once, which is worth stating plainly so the next person does not
mistake the current patterns for the point.

* While k-wise was the default (PR #121) a ballot was four models or two depending on whether
  the task could fill a quad, so any fixed count was wrong for half of all ballots. This file
  banned counts.
* The default is pairwise again (see `_build_ballot`). Every default ballot is two models, so
  the hedge — "four where the task can fill a quad, two otherwise" — is now the thing that
  misdescribes the product, and a plain "two" is correct. This file bans the hedge.

What did NOT change is the failure mode it exists for: ballot-shape copy lives in at least five
places (home.html, arena.html, base.html, README.md, CITATION.cff), a shape change updates the
ones someone happens to grep, and the stale ones stay valid English while every other test
passes. Both times, the copy above the fold was fixed and the rest was not.

**Scope is deliberate.** `methodology.html` is EXCLUDED, and the repo-doc patterns are narrower
than the template ones. Their "pairwise" language is about the STATISTICS and is correct under
either default — Bradley-Terry genuinely fits pairwise decompositions, and a pick-best-of-four
writes three pairwise rows (verified against production: one k-wise submission, three `vote`
rows). K-wise still exists behind `?set=kwise`, so that decomposition is not hypothetical.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

#: Templates a first-time visitor reads before or while voting. `methodology.html` is absent on
#: purpose — see the module docstring.
VISITOR_FACING = ["home.html", "arena.html", "base.html"]

#: Elements whose text `arena.js` REWRITES per rendered ballot (`setBallotHelp`). The template
#: markup is the two-model default; the opt-in four-model render replaces it before any voter
#: reads it. Everything outside these two ids is served as written. Keep in sync with arena.js.
JS_MANAGED_IDS = ["onboard-vote-step", "onboard-keys"]

#: Phrases that describe a ballot the default no longer serves. Each is paired with what to say
#: instead, so a failure tells the next person what the fix is.
#:
#: These name the DEFECT — a four-model claim, or a hedge about which shape a visitor will get —
#: rather than sentences someone once wrote. The previous generation of this list learned that
#: the hard way: its first six patterns were the literal strings one audit happened to find, and
#: a day later four fresh ballot-size assertions shipped on the homepage because the wording
#: differed. Widen these when they miss something; do not add exceptions.
BANNED = [
    (
        r"\bfour\b[^.<]{0,40}?\b(?:models?|reconstructions?|outputs?|generations?|meshes)\b",
        'the default ballot is a pair: say "two"',
    ),
    (
        r"\b(?:models?|reconstructions?|outputs?|generations?|meshes)\b[^.<]{0,40}?\bfour\b",
        'the default ballot is a pair: say "two"',
    ),
    (r"\bfill a quad\b", "the default ballot is always a pair; drop the hedge"),
    (r"\bquad\b", "a k-wise-era word for a shape visitors no longer get by default"),
    (
        r"\bwhere the (?:task|organism) can\b",
        "the shape no longer varies by task; state the pair plainly",
    ),
]


def _strip_jinja_comments(text: str) -> str:
    """Drop `{# ... #}` blocks. A comment is not visitor-facing, and several of them quote the
    banned strings verbatim to record what the copy used to say."""
    return re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)


def _strip_html_comments(text: str) -> str:
    """Drop `<!-- ... -->` blocks, for the same reason as Jinja comments.

    The previous version of this guard stripped only Jinja comments, which was an accident of
    which comment syntax the offending templates happened to use. `arena.html` documents the
    opt-in four-model grid in an HTML comment — necessary explanation that no visitor reads.
    """
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _strip_js_managed(text: str) -> str:
    """Drop elements whose text `arena.js` replaces per ballot shape.

    Their markup is a default, not a promise. Without this the guard would force the default
    copy to be shape-neutral too, which would leave a two-model voter reading vaguer
    instructions than the code can give them.
    """
    for el_id in JS_MANAGED_IDS:
        text = re.sub(rf'<(\w+)[^>]*\bid="{el_id}"[^>]*>.*?</\1\s*>', "", text, flags=re.DOTALL)
    return text


def _visitor_text(name: str) -> str:
    raw = (TEMPLATES / name).read_text()
    return _strip_js_managed(_strip_html_comments(_strip_jinja_comments(raw)))


@pytest.mark.parametrize("name", VISITOR_FACING)
def test_visitor_copy_describes_the_default_pairwise_ballot(name):
    body = _visitor_text(name)
    for pattern, remedy in BANNED:
        found = re.search(pattern, body, flags=re.IGNORECASE)
        assert not found, (
            f"{name} contains {found.group(0)!r}, which describes a ballot shape the arena no "
            f"longer serves by default. Every default ballot is two models. Instead: {remedy}."
        )


#: Verbatim from `home.html` and `arena.html` as shipped while k-wise was the default. Kept as a
#: fixture so the patterns above are pinned to copy that really was served — if a later
#: narrowing stops them firing here, the stale-copy hole reopens quietly.
SHIPPED_COPY_FROM_THE_KWISE_ERA = """
    <h3>The candidate reconstructions</h3>
    <p>
      Several models build 3D from that same input — four where the organism can
      fill a quad, two where it cannot. You inspect each in an interactive viewer:
      orbit, zoom, pan.
    </p>
      One click on the best reconstruction. Where a ballot holds only a pair, tie
      and both-bad are offered as well.
"""


def test_the_patterns_actually_fire_on_the_copy_that_shipped():
    """The negative control: a guard nobody has watched fail is not a guard.

    This is the copy the homepage served under the k-wise default. It is now wrong — a visitor
    reading it would be told they might get four models when they will always get two — so the
    patterns above must catch it. If this ever passes trivially, the guard has been relaxed into
    decoration.
    """
    body = _strip_js_managed(
        _strip_html_comments(_strip_jinja_comments(SHIPPED_COPY_FROM_THE_KWISE_ERA))
    )
    hits = {p for p, _ in BANNED if re.search(p, body, flags=re.IGNORECASE)}
    assert len(hits) >= 3, (
        "the patterns no longer catch the hedged copy that actually shipped on the homepage; "
        f"only {len(hits)} of them fire. Re-widen them rather than deleting this."
    )


def test_the_current_templates_are_not_vacuously_clean():
    """Positive control for the guard's reach.

    Every pattern above is a prohibition, so all three templates would pass if `_visitor_text`
    returned an empty string — a broken strip, a renamed file, a changed comment syntax. Assert
    the scanned text still contains the copy this guard is supposed to be reading.
    """
    body = _visitor_text("home.html")
    assert "judged side by side" in body, (
        "home.html's hero lede is missing from the scanned text — the strips are eating "
        "visitor-facing copy, so the prohibitions above are passing on nothing."
    )
    assert len(_visitor_text("arena.html")) > 2000, "arena.html scanned text is implausibly short"


def test_the_js_managed_exemption_is_exercised_and_narrow():
    """Positive control for `_strip_js_managed`.

    Two ways this exemption goes wrong: it silently matches nothing (then the arena default copy
    would fail the guard for no reason), or it swallows so much of the file that the guard passes
    vacuously. Assert both directions.
    """
    raw = _strip_html_comments(_strip_jinja_comments((TEMPLATES / "arena.html").read_text()))
    stripped = _strip_js_managed(raw)
    assert re.search(r"\bpick the better one\b", raw, flags=re.IGNORECASE), (
        "arena.html no longer carries the two-model default inside #onboard-vote-step, so "
        "this exemption is now unexercised — delete it rather than leaving it unverified."
    )
    assert not re.search(r"\bpick the better one\b", stripped, flags=re.IGNORECASE), (
        "the JS-managed strip did not remove #onboard-vote-step — its markup shape probably "
        "changed. Fix the regex; do not add the phrase back to BANNED's exceptions."
    )
    removed = len(raw) - len(stripped)
    assert 0 < removed < len(raw) * 0.1, (
        f"the exemption removed {removed} of {len(raw)} chars. It is meant to cover two small "
        "help strings; anything larger means the guard is scanning far less than it claims."
    )


#: Repo-level documents that describe the product to someone who has not used it. Guarding the
#: templates alone guarded the tripwire, not the mechanism: when the k-wise default shipped, the
#: README and CITATION.cff carried the stale pairwise description for a day; when the hedge
#: shipped, both carried the hedge. CITATION.cff feeds a permanent Zenodo record.
DESCRIBES_THE_PRODUCT = ["README.md", "CITATION.cff"]

#: Deliberately NARROWER than BANNED above. These files legitimately discuss pairwise STATISTICS
#: ("pairwise significance", "three pairwise comparisons"), and legitimately document the
#: `?set=kwise` opt-in, so a blanket ban on four-model language would be wrong here. Each pattern
#: asserts what a DEFAULT ballot holds.
BANNED_IN_DOCS = [
    (r"\bfour where the task can fill a quad\b", "the default ballot is a pair; drop the hedge"),
    (r"\bfour where the organism can\b", "the default ballot is a pair; drop the hedge"),
]


@pytest.mark.parametrize("name", DESCRIBES_THE_PRODUCT)
def test_repo_docs_describe_the_default_pairwise_ballot(name):
    body = (Path(__file__).resolve().parent.parent / name).read_text()
    for pattern, remedy in BANNED_IN_DOCS:
        found = re.search(pattern, body, flags=re.IGNORECASE)
        assert not found, (
            f"{name} contains {found.group(0)!r}, which describes a ballot shape the arena no "
            f"longer serves by default. Instead: {remedy}."
        )


def test_repo_docs_keep_their_pairwise_statistics_language():
    """Positive control for the docs guard.

    The patterns above are narrow precisely so "pairwise" survives where it is CORRECT. If
    someone widens them into a blanket ban, the paired bootstrap and the k-wise-to-pairwise
    decomposition both get described wrongly — and a wrong statistical claim in CITATION.cff
    would be minted into a DOI.
    """
    root = Path(__file__).resolve().parent.parent
    assert "pairwise significance" in (root / "README.md").read_text().lower(), (
        "README lost its pairwise-significance language. The paired bootstrap genuinely is "
        "pairwise — a statistical claim, not a ballot-size one."
    )
    assert "pairwise" in (root / "CITATION.cff").read_text().lower(), (
        "CITATION.cff lost its pairwise language. A pick-best-of-four is recorded as three "
        "pairwise comparisons; the statistics are pairwise either way."
    )


def test_methodology_keeps_its_pairwise_language():
    """The positive control: this guard must not be 'fixed' by purging the word everywhere.

    Without this, someone could satisfy the tests above by rewriting methodology.html too, which
    would make the statistical description WRONG. A rule that only forbids is a rule that gets
    over-applied.
    """
    body = (TEMPLATES / "methodology.html").read_text()
    assert "pairwise" in body, (
        "methodology.html lost its pairwise language. Bradley-Terry is fitted on pairwise "
        "comparisons — that description is correct and is not what the ballot-shape rule is "
        "about. Revert it."
    )
