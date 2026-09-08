# Engagement push — September 2026

Goal: inbound links and real voters. The site is fully indexed (Search Console: 85 of 87 pages,
2026-09-07) but nothing links to it, so no query surfaces it. Technical SEO is done; do not
re-audit it. Every channel below gets its own campaign tag so `/study` attribution shows which
one produced voters, not just visitors.

| channel | link to post | cohort tag | who posts |
| --- | --- | --- | --- |
| Show HN | `https://taxon3d.org/arena?c=hn` | `hn` | you, from your HN account |
| Hugging Face model Community tabs | `https://taxon3d.org/models/<slug>?c=hf-<model>` | `hf-<model>` | you |
| plant phenotyping community / scan-dataset authors | `https://taxon3d.org/arena?c=pheno` | `pheno` | you |
| r/MachineLearning (optional) | `https://taxon3d.org/arena?c=r-ml` | `r-ml` | you |
| X / Bluesky / LinkedIn | `https://taxon3d.org/arena?c=social` | `social` | you |
| generator teams (email) | `https://taxon3d.org/models/<slug>?c=team-<name>` | `team-<name>` | you |
| awesome-lists (PRs) | `https://taxon3d.org` (plain, it is a permanent link) | none | PR from the repo |

Order that respects the single Fly machine (Cloudflare caches meshes and HTML at the edge, so a
front-page spike is survivable, but do not stack channels on one day):

1. Awesome-list PRs first — they are slow and cost nothing (`awesome-lists.md`).
2. Generator-team emails the same day (`generator-teams.md`). Teams share independent results.
3. Hugging Face Community posts on the four model pages, one per day (`model-community-posts.md`).
4. Show HN on a weekday morning US time (`show-hn.md`). Reply to every comment for 6 hours.
5. Social threads the day of and the day after HN (`social.md`); phenotyping contacts the
   same week (`model-community-posts.md`, second section). Reddit is optional and r/ML only.

Measure after each: `/study` cohort counts, `/leaderboard` firm counts, Search Console links
report (Links → Top linking sites) a week later.

Copy rules used throughout: measured numbers only (1,461 votes, 52 entrants, 488 votable outputs,
20 tasks across plants, fungi and animals, 4 boards); no "first"/"only" claims — 3DGen-Bench has
a Plant category and 3D Arena is the bigger generic arena. The seam is the biological criterion
and the admissibility gate, not "plants".

## Log

| date (EDT) | channel | tag | status |
| --- | --- | --- | --- |
| 2026-09-07 | Awesome-Text-to-3D PR #150 | — | open, awaiting maintainer |
| 2026-09-07 | Tripo / VAST email to support@tripo3d.ai (`sent/2026-09-07-tripo.txt`) | `team-tripo` | sent; one team at a time, next is Meshy after a few days |

