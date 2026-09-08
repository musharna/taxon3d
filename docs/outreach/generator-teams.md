# Generator-team outreach

Teams whose models are on the board have a reason to share an independent result. Send one
email per team, from you, plain text, no attachments. Link their model page with a team tag so
the traffic is attributable. Do not send a rank they would dislike without the CI beside it.

## Targets (image→3D and text→3D boards, ranked entrants only)

| team | models on the board | page to link |
| --- | --- | --- |
| Meshy | Meshy 6, Meshy v6 text | /models/fal:meshy-v6?c=team-meshy |
| Tripo (VAST) | Tripo H3.1 text, Tripo P1 text | /models/fal:tripo-h31-text?c=team-tripo |
| Tencent Hunyuan3D | v2, v3, 3.1, and text variants | /models/fal:hunyuan3d-v3?c=team-hunyuan |
| Microsoft TRELLIS | TRELLIS, TRELLIS 2 | /models/replicate:trellis2?c=team-trellis |
| Meta SAM 3D | SAM 3D | /models/fal:sam-3d?c=team-sam3d |
| Deemos Rodin / Hyper3D | Rodin/Hyper3D, Rodin text | /models/fal:hyper3d?c=team-rodin |
| Pixal3D | Pixal3D | /models/fal:pixal3d?c=team-pixal |
| fal.ai, Replicate (hosts) | most of the above | /models?c=team-fal , /models?c=team-replicate |

LLM providers (Anthropic, OpenAI, Google, xAI, DeepSeek, Alibaba Qwen, Moonshot, Zhipu, Meta,
MiniMax, Mistral) are on the procedural and agentic boards, which have no firm ranks yet; write
to them after those boards firm, with a result to show.

## Who to write to (looked up 2026-09-07)

| team | recipient | why this door |
| --- | --- | --- |
| Microsoft TRELLIS | Jiaolong Yang, jiaoyan [at] microsoft.com (Principal Researcher, MSRA; last author) — cc Jianfeng Xiang, t-jxiang [at] microsoft.com (first author) | research group; authors read their own mail |
| Tencent Hunyuan3D | Discord https://discord.gg/dNBrdrGGMa (a "results" post) and X @TencentHunyuan; team lead is Chunchao Guo, no public email | the README lists no email; Discord is the official channel |
| Tripo / VAST | support@tripo3d.ai with subject starting "For the research team:", and X @VastAIResearch | only published address; VAST is a research lab, so support forwards |
| Meshy | the meshy.ai/contact form with topic **"Other"** — Meshy support (09-08) says that routes to their MARKETING team; support@meshy.ai auto-closes partnership mail and points back to the form | no research/press address published; the form looks sales-only but the topic field is the router |

For Hunyuan3D and Meshy the Hugging Face Community post (`model-community-posts.md`) is the
stronger channel; send the email/form as well but expect the community post to be what they see.

## Template

Subject: <Model> on Taxon3D, a blind human-vote benchmark for 3D models of organisms

Hi <name>,

I run Taxon3D (taxon3d.org), a blind pairwise arena that ranks 3D generative models on living
organisms: maize, rose, lion's mane, goldfish, monarch, and others, always with reference
photographs of the real organism beside the pair. Rankings are Bradley–Terry with bootstrap
confidence intervals.

<Model> is on the <board> board: <rank sentence with CI, e.g. "rank 1 of 12 on image→3D,
tied with TRELLIS 2 within the interval, 117 games">. The page is here:
<link with team tag>

Two things you might want:
- The model page shows the head-to-head record against every opponent it has met, and every
  leaderboard vote is in the Hugging Face preferences table with generators revealed.
- If a newer version exists, I will run it on the same tasks; the submission form is at
  taxon3d.org/submit.

If you share the result, a link back is what keeps the site findable. Happy to answer any
question about the protocol.

<your name>
