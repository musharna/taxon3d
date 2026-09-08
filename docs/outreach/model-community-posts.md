# Model-page community posts (Hugging Face)

Checked 2026-09-07: none of microsoft/TRELLIS, Tencent-Hunyuan/Hunyuan3D-2,
facebookresearch/sam-3d-objects or VAST-AI-Research/TripoSR has GitHub Discussions enabled, and
opening an issue for this would be closed on sight. The equivalent that exists is the Community
tab on each model's Hugging Face page, read by the model's users and watched by its authors.
One post per model, from your account, linking the model page with a tag.

| Hugging Face model (Community tab) | likes | model page to link |
| --- | --- | --- |
| microsoft/TRELLIS-image-large | 675 | https://taxon3d.org/models/replicate:trellis2?c=hf-trellis |
| tencent/Hunyuan3D-2 | 1,811 | https://taxon3d.org/models/fal:hunyuan3d-v3?c=hf-hunyuan |
| facebook/sam-3d-objects | 464 | https://taxon3d.org/models/fal:sam-3d?c=hf-sam3d |
| stabilityai/TripoSR | 657 | https://taxon3d.org/models/fal:triposr?c=hf-triposr |

(tencent/Hunyuan3D-3 is gated; skip.)

**Title:** Results for <Model> on living organisms (blind human votes, with reference photos)

**Body:**

I run Taxon3D, a blind pairwise arena where people compare two anonymised 3D models of a named
organism, with CC-licensed photos of the real thing beside them. <Model> is on the image→3D
board: <rank sentence with CI and games>. Its head-to-head record against every opponent it has
met is here: <link>

Every task ships the input photo, so you can see exactly what the model was given. The set is
plants, fungi and animals, which are harder than props: thin surfaces, branching, self-occlusion.

Two things that might be useful to this project: the failure modes are visible per task (which
organisms it struggles with), and if a newer checkpoint exists I will run it on the same tasks.
Code is MIT, votes are on Hugging Face.

Happy to answer questions about the protocol here.

Keep it to one post per model; a second one reads as spam.

# Plant phenotyping community

The one audience that cares whether a generated plant is anatomically right and is not hostile
to AI. Where they are: Bluesky (plant science and phenotyping accounts), the IPPN community, and
the authors of the scan datasets already in the corpus (Plant3D at Salk, Crops3D, ROSE-X,
ICRISAT legumes, ROMI). Email the dataset authors first: their scans are on the site as
reference material, credited on /licenses, and that is a reason for them to look.

Framing for this audience: "how far generative 3D is from a usable plant model, measured
against your scans", not "AI makes plants".

# Reddit — optional, r/MachineLearning only

Art and 3D subreddits treat anything AI-adjacent as slop; a benchmark post buys hostility, not
links. If posting at all, r/MachineLearning with the [P] flair, using the Show HN text. Skip
r/computervision and r/3Dmodeling.
