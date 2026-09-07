# scripts/backup_to_r2.py
"""Upload one prod-DB pull to an S3-compatible bucket (Cloudflare R2) and prune old copies.

Prod is one SQLite file on one Fly volume; Fly keeps daily snapshots for 5 days and nothing else
exists off the machine between manual pulls (repo audit 2026-09-06). This is the scheduled copy.
`scripts/pull_prod_db.sh` makes the consistent file (VACUUM INTO); this script only moves it.

Env (all required, no fallbacks — a missing secret must fail loud, not upload nowhere):
  BACKUP_S3_ENDPOINT, BACKUP_S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
Usage:
  python scripts/backup_to_r2.py data/prod-pulls/arena.<stamp>.db [--keep-days 30] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PREFIX = "prod-db/"


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} is not set")
    return v


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_prune(
    keys_with_dates: list[tuple[str, datetime]], *, keep_days: int, now: datetime, min_keep: int = 7
) -> list[str]:
    """Keys older than `keep_days`, but never so many that fewer than `min_keep` copies remain.
    A retention rule that could delete everything is a deletion rule with a delay."""
    ordered = sorted(keys_with_dates, key=lambda kd: kd[1], reverse=True)
    cutoff = now - timedelta(days=keep_days)
    return [k for k, d in ordered[min_keep:] if d < cutoff]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("db", type=Path)
    ap.add_argument("--keep-days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.db.is_file() or args.db.stat().st_size == 0:
        sys.exit(f"{args.db} is missing or empty")

    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=_env("BACKUP_S3_ENDPOINT"),
        aws_access_key_id=_env("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_env("AWS_SECRET_ACCESS_KEY"),
        region_name="auto",
    )
    bucket = _env("BACKUP_S3_BUCKET")
    key = PREFIX + args.db.name
    digest = sha256(args.db)
    print(
        f"upload {args.db} -> s3://{bucket}/{key} ({args.db.stat().st_size} bytes, sha256 {digest[:16]}…)"
    )
    if not args.dry_run:
        s3.upload_file(str(args.db), bucket, key, ExtraArgs={"Metadata": {"sha256": digest}})
        head = s3.head_object(Bucket=bucket, Key=key)
        if head["ContentLength"] != args.db.stat().st_size:
            sys.exit("uploaded size differs from local size")

    listed = s3.list_objects_v2(Bucket=bucket, Prefix=PREFIX).get("Contents", [])
    doomed = to_prune(
        [(o["Key"], o["LastModified"]) for o in listed],
        keep_days=args.keep_days,
        now=datetime.now(UTC),
    )
    for k in doomed:
        print(f"prune {k}")
        if not args.dry_run:
            s3.delete_object(Bucket=bucket, Key=k)
    print(f"kept {len(listed) - len(doomed)} copies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
