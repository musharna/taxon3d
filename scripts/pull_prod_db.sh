#!/usr/bin/env bash
# Pull a consistent snapshot of the PRODUCTION database off the Fly volume.
#
# Production is a SQLite file on one Fly volume in one region (fly.toml [[mounts]]); votes cast
# there are the only copy until this script has run and scripts/harvest_live_votes.py has moved
# them into data/study/arena-study.db. So this script IS the backup — Fly's own snapshots are
# daily with 5-day retention, and there is no replica.
#
# Why VACUUM INTO and not `cp` / `sftp get /data/arena.db`: the database runs in WAL mode, so a
# byte copy of the main file silently drops whatever is still in `-wal` (80 rescued votes were
# lost that way on 2026-07-26). VACUUM INTO writes a single self-contained file from a read
# transaction, and needs no app downtime.
#
# Usage:
#   scripts/pull_prod_db.sh              # -> data/prod-pulls/arena.<UTC stamp>.db
#   scripts/pull_prod_db.sh --dest PATH  # explicit destination (still refuses to overwrite)
#
# Then (deploy/README.md, "Harvest before every release"):
#   env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH \
#     BIO3D_PUBLIC_DATABASE_URL="sqlite:///$PWD/data/prod-pulls/arena.<stamp>.db" \
#     python scripts/harvest_live_votes.py --dry-run     # then --apply
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# flyctl installs to ~/.fly/bin, which is not on a non-interactive PATH.
export PATH="$HOME/.fly/bin:$PATH"
command -v flyctl >/dev/null || {
	echo "flyctl not found (expected in ~/.fly/bin)" >&2
	exit 1
}

# The app name comes from fly.toml, not a hard-coded string, so this cannot drift from the deploy.
APP="$(sed -n '/^app *= */{s/^app *= *"\([^"]*\)".*/\1/p;q;}' fly.toml)"
[[ -n "$APP" ]] || {
	echo "could not read app name from fly.toml" >&2
	exit 1
}

REMOTE_DB="/data/arena.db"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_TMP="/data/pull-${STAMP}.db"
DEST="data/prod-pulls/arena.${STAMP}.db"

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dest)
		DEST="$2"
		shift 2
		;;
	-h | --help)
		sed -n '2,20p' "$0"
		exit 0
		;;
	*)
		echo "unknown argument: $1" >&2
		exit 2
		;;
	esac
done

# Never overwrite: a pull is evidence of what prod held at one instant, and two pulls that
# share a name would make one of them a lie.
if [[ -e "$DEST" ]]; then
	echo "refusing to overwrite existing $DEST" >&2
	exit 1
fi
mkdir -p "$(dirname "$DEST")"

cleanup_remote() {
	# Best-effort: the temp copy on the volume is only clutter once it is here, but it holds a
	# full copy of every vote, so do not leave it behind if we can help it.
	flyctl ssh console -a "$APP" -C "rm -f $REMOTE_TMP" >/dev/null 2>&1 ||
		echo "WARNING: could not remove $REMOTE_TMP on the machine; remove it by hand" >&2
}
trap cleanup_remote EXIT

echo "== $APP: VACUUM INTO $REMOTE_TMP"
# python3 is what the image has (python:3.12-slim); the sqlite3 CLI is not installed there.
# The remote command is one line because `-C` takes a single argv string.
flyctl ssh console -a "$APP" -C "python3 -c \"import sqlite3; c = sqlite3.connect('file:${REMOTE_DB}?mode=ro', uri=True); c.execute('VACUUM INTO ?', ('${REMOTE_TMP}',)); c.close(); import os; print('remote bytes', os.path.getsize('${REMOTE_TMP}'))\""

echo "== sftp get $REMOTE_TMP -> $DEST"
# `sftp get` refuses to clobber a local file, which is a second guard on the check above.
flyctl ssh sftp get "$REMOTE_TMP" "$DEST" -a "$APP"
[[ -s "$DEST" ]] || {
	echo "pull produced no file at $DEST" >&2
	exit 1
}

echo "== verifying"
python3 - "$DEST" <<'PY'
import sqlite3, sys
p = sys.argv[1]
c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
(ok,) = c.execute("PRAGMA integrity_check").fetchone()
if ok != "ok":
    sys.exit(f"integrity_check on {p}: {ok}")
for t in ("vote", "comparison", "voter_session"):
    (n,) = c.execute(f"SELECT count(*) FROM {t}").fetchone()
    print(f"  {t:14s} {n}")
(mx,) = c.execute("SELECT max(id) FROM vote").fetchone()
print(f"  max(vote.id)   {mx}")
PY

echo "== local bytes $(stat -c %s "$DEST")"
echo "== sha256"
sha256sum "$DEST"
echo
echo "Pulled $DEST. Next: harvest it (deploy/README.md, 'Harvest before every release')."
