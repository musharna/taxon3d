# Reddit

Post one subreddit per day. Use the subreddit's tag conventions. Answer comments the same day.

## r/MachineLearning — flair [P]

**Title:** [P] Taxon3D: a blind pairwise arena that ranks 3D generative models on living
organisms (plants, fungi, animals), with an admissibility gate before voting

**Body:**

Live: https://taxon3d.org/arena?c=r-ml · Code (MIT): https://github.com/musharna/taxon3d ·
Data: https://huggingface.co/datasets/musharna/taxon3d-corpus-v1

Setup: a biological task (single reference photo or text prompt of a named organism), two
anonymised outputs, reference photographs beside them, one vote. Bradley–Terry with bootstrap
95% CIs; ranks are CI-grouped so models the votes cannot separate share a rank.

What is different from generic 3D arenas:
- The criterion is anatomical. Reference photos of the real organism sit next to every pair.
- Outputs pass a fail-closed admissibility gate (single whole organism, non-degenerate mesh)
  before they can be voted on. A missing verdict means not admitted.
- Four paradigms ranked separately: image→3D, text→3D, LLM-authored procedural geometry, and
  agentic render→critique→revise. The two LLM boards opened to public voting this week.

Scale is honest: 1,461 votes, 52 entrants, 20 tasks. Each board states the votes it still needs.

I would value criticism of the gate design and the ranking choices more than votes.

## r/computervision

**Title:** Blind arena for image→3D reconstruction of real organisms — Meshy, TRELLIS,
Hunyuan3D, SAM 3D, Rodin, Tripo ranked by human votes with CIs

**Body:** short version of the above, lead with the image→3D board:
https://taxon3d.org/leaderboard?paradigm=image_recon&c=r-cv . Mention that every task ships
the input photo and the reference photos, so you can judge the judges.

## r/3Dmodeling

**Title:** Which AI actually rebuilds a plant in 3D? Vote blind between two models, see the
real thing next to them

**Body:** casual, no methods. https://taxon3d.org/arena?c=r-3d . Ask which organisms they want
added. Expect "AI slop" comments; answer that the point is measuring exactly that.
