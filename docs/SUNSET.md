# Sunset policy

Decided 2026-09-24. Active development, outreach and the paper studies (C′, A′) have stopped.
The public dataset (`musharna/taxon3d-corpus-v1`) and the Zenodo DOI (10.5281/zenodo.21789280)
are the lasting outputs. The arena stays up in maintenance mode until the review date.

## Review date: 2027-03-24

On that date, shut the arena down unless one of the reasons to keep it has happened.

**Keep it running past the review if any of these hold:**

- someone outside the project cites or uses the arena itself, not just the dataset
- a collaborator has taken an interest in continuing the work
- organic votes (untagged sessions that are not the maintainer's) have reached 50 or more

**Shut down early if any of these hold:**

- the monthly bill rises noticeably above the 2026-09-24 baseline of about $10 on Fly
- the site needs real maintenance (a security fix, a dependency break, a Fly platform change)
- the maintainer no longer wants it running

## While it runs

Prod votes exist only on the Fly volume until harvested. Harvest every one to two months, and
always before shutdown: `VACUUM INTO` on the machine, `flyctl ssh sftp get`, verify the vote-id
overlap, then `scripts/harvest_live_votes.py --dry-run` and `--apply`. Last harvest: 2026-09-24,
study at 1,803 votes.

## Shutdown checklist

1. Final harvest of prod votes (above).
2. Optional: a final dataset refresh on the Hub carrying the last votes.
3. Point `taxon3d.org` at a single static page linking the dataset and the DOI, so existing
   links keep resolving.
4. Destroy the Fly apps (`bio3d-arena`, `bio3d-log-shipper`) and the `bio3d_data` volume.
   Decide whether to keep the R2 meshes; the dataset already carries the redistributable ones.
5. Archive the GitHub repo read-only, with a note at the top of the README.
6. Decide whether to renew the domain.
