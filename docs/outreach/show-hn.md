# Show HN

**Title (≤80 chars):**
Show HN: Taxon3D – blind arena ranking AI 3D models on real organisms

**Text:**

I built a Chatbot-Arena-style site for 3D generation, but the subjects are living things:
maize, roses, lion's mane mushrooms, goldfish, monarch butterflies. You get a task, two
anonymised 3D models, reference photos of the real organism, and you pick the closer one.
Votes go into Bradley–Terry rankings with bootstrap confidence intervals.

https://taxon3d.org/arena?c=hn

Why organisms: most 3D-gen evaluation is furniture and game props, where "looks plausible" is
good enough. A plant has self-similar branching, thin surfaces and heavy self-occlusion, and
the correctness question is anatomical, not aesthetic. The site puts CC-licensed reference
photos beside every pair so the voter is answering "is this the organism" rather than "is
this pretty".

Four boards, ranked separately because the match pools do not overlap:
- image→3D reconstruction (Meshy, TRELLIS, Hunyuan3D, SAM 3D, Rodin, …)
- text→3D
- LLMs writing procedural geometry code (Claude, GPT, Gemini, Grok, DeepSeek, Qwen, …)
- agentic loops: an LLM renders, critiques and revises its own model

The LLM boards just opened to public voting today, so they are the ones where your vote
moves a rank.

Things I'd rather you know than find out:
- 1,461 votes so far, 52 entrants, 488 votable outputs, 20 tasks. Small. Every board says on
  its face how many more votes it needs before the next rank is firm.
- Outputs pass an admissibility gate before anyone votes on them (whole single organism,
  non-degenerate mesh). Rejected outputs are excluded, not ranked last. The gate is a VLM plus
  geometry checks and it fails closed; a human audit of it is designed but not yet run.
- Code is MIT (github.com/musharna/taxon3d). The vote table and the redistributable meshes are
  on Hugging Face as musharna/taxon3d-corpus-v1. Closed models' outputs are display-only.
- One small Fly machine behind Cloudflare. If it falls over, that is why.

I want to hear which comparisons feel unfair, and which organisms you'd add.

**Reply kit** (have these ready):
- "Why not just use CLIP/FID?" → reference-based metrics need a ground-truth mesh, which does
  not exist for most organisms; the human criterion is the point.
- "Sample sizes are tiny" → agreed, the CIs are on the board and the rank groups collapse
  when votes cannot separate models.
- "LLM code-gen is not 3D generation" → it is one paradigm of four, ranked only against itself.
- "Is this a paper?" → not yet; the evidence plan is public in docs/paper.
