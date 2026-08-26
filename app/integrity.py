"""Vote-integrity / anti-abuse primitives for the public arena.

- Sliding-window rate limiting (in-process; production should back this with Redis).
- Per-session dedup so one voter can't farm the same pairing repeatedly.
- Gold attention-check trust scoring (Laplace-smoothed pass rate).
- Pluggable captcha verification (off unless BIO3D_REQUIRE_CAPTCHA).
"""

from __future__ import annotations

import functools
import json as _json
import random
import time
import urllib.parse as _urlparse
import urllib.request as _urlreq
from collections import OrderedDict, defaultdict, deque

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .models import Comparison, KBallot, VoterSession, Vote


class InMemoryRateLimiter:
    """Per-process sliding-window limiter. Fine for a single worker / dev."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int | None = None) -> bool:
        limit = config.VOTE_RATE_LIMIT if limit is None else limit
        now = time.monotonic()
        dq = self._buckets[key]
        cutoff = now - config.VOTE_RATE_WINDOW
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True

    def reset(self) -> None:
        self._buckets.clear()


class RedisRateLimiter:
    """Distributed fixed-window limiter shared across workers (redis is lazy)."""

    def __init__(self, redis_url: str) -> None:
        import redis  # lazy: only when BIO3D_REDIS_URL is configured

        self._redis = redis.Redis.from_url(redis_url)

    def allow(self, key: str, limit: int | None = None) -> bool:
        limit = config.VOTE_RATE_LIMIT if limit is None else limit
        window = int(config.VOTE_RATE_WINDOW)
        # Bucket key per window slot → fixed-window counter with auto-expiry.
        slot = int(time.time()) // window
        rkey = f"bio3d:rl:{key}:{slot}"
        count = self._redis.incr(rkey)
        if count == 1:
            self._redis.expire(rkey, window)
        return count <= limit

    def reset(self) -> None:  # best-effort; used by tests (not against real redis)
        pass


@functools.lru_cache(maxsize=1)
def _limiter():
    return RedisRateLimiter(config.REDIS_URL) if config.REDIS_URL else InMemoryRateLimiter()


def check_rate_limit(session_id: str) -> bool:
    """Returns True if the vote is allowed, False if over the per-session limit."""
    return _limiter().allow(session_id)


def check_ip_rate_limit(ip: str) -> bool:
    """Per-IP limit (own `ip:` namespace, more generous cap) — caps throughput even when a farmer
    clears their session cookie. Returns True if allowed, False if over IP_VOTE_RATE_LIMIT."""
    return _limiter().allow(f"ip:{ip}", limit=config.IP_VOTE_RATE_LIMIT)


def reset_rate_limits() -> None:
    """Clear rate state (used by tests)."""
    _limiter().reset()


_SITEVERIFY = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
}


def _post_form(url: str, data: dict) -> dict:
    body = _urlparse.urlencode(data).encode()
    # urllib auto-sets this for bytes `data`, but be explicit — the siteverify
    # endpoints expect a form-encoded body.
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    with _urlreq.urlopen(_urlreq.Request(url, data=body, headers=headers), timeout=10) as r:
        return _json.loads(r.read().decode())


def verify_captcha(token: str | None, *, _post=_post_form) -> bool:
    """Verify a human-check token. No-op unless REQUIRE_CAPTCHA is enabled.

    When enabled, POSTs `token` to the configured provider's siteverify endpoint
    (Turnstile/hCaptcha). `_post` is injectable for testing; network/parse failure
    fails closed (returns False) since a required captcha must not silently pass.
    """
    if not config.REQUIRE_CAPTCHA:
        return True
    if not token:
        return False
    url = _SITEVERIFY.get(config.CAPTCHA_PROVIDER, _SITEVERIFY["turnstile"])
    try:
        res = _post(url, {"secret": config.CAPTCHA_SECRET, "response": token})
        return bool(res.get("success")) if isinstance(res, dict) else False
    except Exception:
        return False


# Sessions that have passed a challenge. In-process and deliberately not persisted: it is
# soft state (losing it costs one extra challenge, never a wrong vote), and the arena runs a
# single process. Bounded so a flood of one-shot sessions cannot grow it without limit.
_CAPTCHA_VERIFIED: "OrderedDict[str, bool]" = OrderedDict()
_CAPTCHA_VERIFIED_MAX = 50_000


def reset_captcha_sessions() -> None:
    """Clear remembered verifications (used by tests)."""
    _CAPTCHA_VERIFIED.clear()


def captcha_ok_for_session(
    db: Session, session_id: str, token: str | None, *, _post=_post_form
) -> bool:
    """Verify a voter ONCE per session rather than once per vote.

    Turnstile/hCaptcha tokens are single-use and short-lived, so checking one on every vote
    means a challenge round-trip per vote. This arena's binding constraint is vote VOLUME, so
    per-vote friction would cost more than the automation it deters. One challenge per session,
    then the session carries it.

    The verification is PERSISTED on the voter's session row, not held in process memory. It
    used to live in a module-level dict, which meant a restart, a deploy, or an auto-suspend
    forgot every verified voter — and the browser cannot recover from that, because its token
    is single-use and the widget has already fired its callback. The voter was simply locked
    out mid-session, reported from the live instance as the captcha "having occasional issues
    staying authorized". Session state belongs with the session.

    The in-memory map is kept purely as a read-through cache to save a query per vote; it is
    never the source of truth, so losing it costs one SELECT rather than one voter.

    Fails closed in both directions: an unverified session with no token is refused, and a
    REJECTED token leaves the session unverified, so the next vote is challenged again instead
    of being waved through.
    """
    if not config.REQUIRE_CAPTCHA:
        return True
    if _CAPTCHA_VERIFIED.get(session_id):
        _CAPTCHA_VERIFIED.move_to_end(session_id)
        return True

    row = db.get(VoterSession, session_id)
    if row is not None and row.captcha_verified:
        _remember(session_id)
        return True

    if not verify_captcha(token, _post=_post):
        return False

    if row is None:
        row = get_or_create_session(db, session_id)
    row.captcha_verified = True
    db.flush()
    _remember(session_id)
    return True


def _remember(session_id: str) -> None:
    """Cache a known-verified session, bounded so one-shot sessions can't grow it forever."""
    _CAPTCHA_VERIFIED[session_id] = True
    _CAPTCHA_VERIFIED.move_to_end(session_id)
    while len(_CAPTCHA_VERIFIED) > _CAPTCHA_VERIFIED_MAX:
        _CAPTCHA_VERIFIED.popitem(last=False)  # evict least-recently-verified


def get_or_create_session(db: Session, session_id: str) -> VoterSession:
    vs = db.get(VoterSession, session_id)
    if vs is None:
        vs = VoterSession(session_id=session_id)
        db.add(vs)
        db.flush()
    return vs


def note_vote(db: Session, session_id: str) -> VoterSession:
    vs = get_or_create_session(db, session_id)
    vs.n_votes += 1
    return vs


def gold_outcome(winner: str, expected: str | None) -> bool | None:
    """Score one answer to an attention check. `None` means ABSTAINED — not observed at all.

    A gold pair is a real output against a degenerate decoy, and it asks exactly one question:
    can you tell them apart. `bad` ("both are bad") and `tie` answer neither yes nor no — they
    decline to prefer, which on a pair where one mesh is genuinely poor can be the honest reply.
    Scoring them as failures measured willingness to pick a winner instead of ability to spot the
    decoy, and the two are not the same trait.

    Measured on the 2026-08-25 recruited pilot: 10 of 12 "failures" were `bad`, only 2 were
    picking the decoy, and 22% of that cohort's real ballots were non-binary. The largest single
    contributor was left at trust exactly 0.500 — the admission threshold — one such answer from
    having all 100 of their votes silently dropped from every board.

    An abstention consumes nothing: the caller must not touch either counter, so the session's
    trust is unchanged and the check can still be put to them again.
    """
    if winner not in ("a", "b"):
        return None
    return winner == expected


def ballots_since_last_gold(db: Session, session_id: str) -> int:
    """How many ballots this session has seen since its last attention check (ever, if none).

    Counts BALLOTS, not relations. A k-wise ballot resolves into K-1 Comparison rows sharing
    one `ballot_id`, so counting comparisons flat would clock a 4-up voter three times per
    ballot; and the k-wise builder writes its KBallot at serve time while those Comparison rows
    only appear on resolution, so counting comparisons alone would miss a ballot that was shown
    and abandoned. Pairwise comparisons (ballot_id NULL) plus KBallot rows counts each ballot
    exactly once on either path.

    Served, not answered, is the unit deliberately. It over-counts a voter who reloads without
    voting, but the error direction is safe: more ballots served brings the next check SOONER,
    never later, so there is no way to spend requests to push a check away.
    """
    last_gold = db.execute(
        select(func.max(Comparison.created)).where(
            Comparison.session_id == session_id, Comparison.is_gold.is_(True)
        )
    ).scalar()

    pairwise = select(func.count()).where(
        Comparison.session_id == session_id,
        Comparison.is_gold.is_(False),
        Comparison.ballot_id.is_(None),
    )
    kwise = select(func.count()).where(KBallot.session_id == session_id)
    if last_gold is not None:
        pairwise = pairwise.where(Comparison.created > last_gold)
        kwise = kwise.where(KBallot.created > last_gold)
    return int(db.execute(pairwise).scalar() or 0) + int(db.execute(kwise).scalar() or 0)


def should_serve_gold(ballots_since: int, gold_seen: int, *, rng=random.random) -> bool:
    """Decide whether this ballot should be an attention check.

    Injection used to be `random.random() < GOLD_RATE` on every ballot, with no reference to
    the session. That makes coverage a by-product of sampling rather than a property: a voter
    escapes measurement entirely with probability (1 - rate)^n. An attention check is a
    qualification on the voter, not a sprinkle on the stream, so it is scheduled instead.

    While a session is UNMEASURED (`gold_seen == 0`) the conditional probability is
    1/remaining, which places the first check uniformly at random among the first
    GOLD_DEADLINE ballots and makes it certain by the last of them. Uniform matters as much as
    certain: a hard "force it at ballot N" would guarantee coverage while telling a farmer
    exactly which ballot to answer honestly, trading a coverage defect for a gaming surface.

    Once a reading exists, the deadline stops applying and checks go back to being occasional
    at GOLD_RATE — otherwise every voter would be re-checked every N ballots forever.

    `gold_seen`, not golds served, is the measured-ness test on purpose: it is the counter an
    abstention deliberately leaves alone (see `gold_outcome`), so a voter who answers "both are
    bad" is still unmeasured and still owed a check. Pacing restarts anyway, because
    `ballots_since_last_gold` keys on the check being SERVED — so an abstainer gets another
    check within a window rather than several in a row.
    """
    if config.GOLD_RATE <= 0.0:
        # Rate 0 is the operator's off switch for attention checks, and the deadline must not
        # override it — a schedule that still forced checks would mean "off" turned nothing off.
        return False
    if gold_seen == 0:
        remaining = config.GOLD_DEADLINE - ballots_since
        if remaining <= 1:
            return True
        # The deadline raises a FLOOR under the configured rate rather than replacing it. Taking
        # the max keeps this a strict strengthening: a rate set above the schedule's own
        # probability still governs, so raising GOLD_RATE cannot be throttled by the very
        # mechanism meant to guarantee coverage.
        return rng() < max(config.GOLD_RATE, 1.0 / remaining)
    return rng() < config.GOLD_RATE


def record_gold_outcome(db: Session, session_id: str, passed: bool) -> VoterSession:
    """Update a session's trust from a gold attention-check outcome.

    Call only with a decided outcome from `gold_outcome`; an abstention (None) must skip this
    entirely, since incrementing `gold_seen` is what makes a check count against a voter.
    """
    vs = get_or_create_session(db, session_id)
    vs.gold_seen += 1
    if passed:
        vs.gold_passed += 1
    # Laplace-smoothed pass rate: starts at 1.0, decays with failures.
    vs.trust = (vs.gold_passed + 1) / (vs.gold_seen + 1)
    return vs


def voted_pairs_for(db: Session, session_id: str, criterion_id: int) -> set[frozenset[int]]:
    """All (unordered) output pairs this session has cast a decided vote on, for a criterion.

    This is the exclusion set matchmaking must honor: the vote endpoint 409s a re-vote of any
    of these pairings, so pick_task/pick_pair must never re-serve one (else a session dead-ends
    on 'already voted' instead of ending cleanly or getting a fresh pair)."""
    rows = db.execute(
        select(Comparison.output_a_id, Comparison.output_b_id)
        .join(Vote, Vote.comparison_id == Comparison.id)
        .where(
            Comparison.session_id == session_id,
            Comparison.criterion_id == criterion_id,
            Comparison.is_gold.is_(False),
        )
    ).all()
    return {frozenset((a, b)) for a, b in rows}


def seen_quads_for(db: Session, session_id: str, criterion_id: int) -> set[frozenset[int]]:
    """Frozensets of the 4 output ids for every KBallot this session already saw for the criterion."""
    import json as _json

    from .models import KBallot

    rows = (
        db.query(KBallot.output_ids_json)
        .filter(KBallot.session_id == session_id, KBallot.criterion_id == criterion_id)
        .all()
    )
    return {frozenset(_json.loads(r[0])) for r in rows}


def already_voted_pair(
    db: Session, session_id: str, output_a_id: int, output_b_id: int, criterion_id: int
) -> bool:
    """True if this session already cast a decided vote on the same (unordered) pair."""
    return frozenset((output_a_id, output_b_id)) in voted_pairs_for(db, session_id, criterion_id)
