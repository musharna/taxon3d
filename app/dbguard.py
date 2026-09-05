"""One shared prod-write gate for every script that mutates a database or an asset in place.

Before this module the guard was a docstring — "NEVER point BIO3D_DATABASE_URL at the study
DB; use a copy" — copied into a dozen scripts. A docstring cannot refuse, and the study DB was
in fact wiped once by a process that read the same environment (2026-06-28). The correct shape
is ONE helper every writer calls, not forty-seven private copies of an `--apply` flag: a fix
to the rule (a new protected URL pattern, a better message) then lands everywhere at once.

Contract:

* `add_write_target_args(parser)` adds `--apply` (default OFF = dry run) and `--allow-study`.
* `confirm_write_target(args, purpose=...)` prints the RESOLVED `config.DATABASE_URL` (password
  redacted) and what the script is about to do, then refuses with `SystemExit(2)` unless
  `--apply` was given. A URL naming the study database (`arena-study`) is refused even with
  `--apply` unless `--allow-study` is also passed, so a copied shell line cannot reach it.

Scripts that want a richer dry-run print their plan BEFORE calling `confirm_write_target`;
the helper itself is the last thing that runs before the first write.
"""

from __future__ import annotations

import argparse
import re
import sys

from . import config

STUDY_MARKER = "arena-study"

_CRED_RE = re.compile(r"(://[^:/@]+:)[^@]*@")


def redact_url(url: str) -> str:
    """Mask the password in `scheme://user:password@host/...`; other URLs pass through."""
    return _CRED_RE.sub(r"\1***@", url)


def add_write_target_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write. Without it the script is a DRY RUN: it resolves and prints the "
        "database it WOULD write to, then exits 2 before touching anything",
    )
    parser.add_argument(
        "--allow-study",
        action="store_true",
        help="permit writing to a database whose URL names the study DB (arena-study). "
        "Refused otherwise even with --apply",
    )
    return parser


def confirm_write_target(args: argparse.Namespace, *, purpose: str) -> str:
    """Print the resolved write target + purpose; return the URL, or exit 2 if not allowed."""
    url = config.DATABASE_URL
    shown = redact_url(url)
    print(f"write target: {shown}")
    print(f"purpose:      {purpose}")
    if not getattr(args, "apply", False):
        print(
            f"DRY RUN — nothing written. Re-run with --apply to {purpose} on {shown}.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if STUDY_MARKER in url and not getattr(args, "allow_study", False):
        print(
            f"REFUSED: {shown} is the STUDY database ({STUDY_MARKER!r} in the URL). "
            "Run against a copy, or pass --allow-study together with --apply if you really "
            "mean the study DB.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return url
