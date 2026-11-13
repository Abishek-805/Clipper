# Task 06D — Anime highlight evaluation and local VLM feasibility

## Result

**Evaluation tooling implemented; local VLM inference not run.** Task 06D adds a human-review board, a versioned annotation dataset, metrics that require at least five human labels, and a provider-neutral visual-analysis contract with a deterministic mock. The production candidate scoring and selection pipeline were not changed. The existing cached S01-E01 analysis was reused; the episode was not processed again.

## Baseline and scope

- Branch: `feature/task-06d-multimodal-evaluation`
- Baseline commit: `b9b07d410f0cdcaceeb4456a3feaabc543b08a45` (Task 06C HEAD)
- Protected `main` and `backup/task-06b-stable-20261112` branches were not modified.
- Existing input: cached analysis for the 1,422.59-second S01-E01 source, containing 2,461 candidates, 387 detected cuts, 2,089 word timestamps and five existing selected clips.
- No source media was changed. Review previews are muted, H.264, 640 pixels wide, 12fps and no longer than eight seconds. Representative frames are JPEGs capped at 480 pixels wide.
- No package, CUDA runtime or model weight was installed or downloaded. One Hugging Face dry-run request queried file metadata only; no model payload was fetched.

## Candidate review board and human annotation

`evaluation/build_review_board.py` builds an offline HTML board from an existing `analysis.json` and sibling `transcription.json`. Its deterministic 36-item review set is spread round-robin across six equal episode regions and draws from selected, dialogue, high-motion, high-audio, opening/ending, and lower-ranked candidates. Invalid-boundary candidates are excluded. These are review strata from existing heuristic evidence, not human or semantic classifications.

Each card has candidate and source times, duration, predicted category (or `Unclassified`), existing selection score/state, heuristic reason, transcript excerpt, signal evidence, three representative frames, and a short preview. A reviewer can choose keep/reject/maybe, a category, nine 1–5 quality ratings, rejection reasons, corrected start/end, and notes. Browser local storage preserves drafts; import/export saves annotations as the dataset JSON. The HTML has no external scripts or services.

The versioned schema is `clipper-anime-evaluation-v1`, in `evaluation/anime_highlight_dataset.schema.json`; data validation and atomic save/load are in `evaluation/anime_review.py`. Model/heuristic predictions live on candidate records and reviewer decisions live separately in `annotations`. The generated initial dataset contains **zero human annotations**; no labels were invented.

The generated board and review data are local, git-ignored artifacts at:

`Shorts_Output/Task06C-validation-20261112-221019/_S01-E01_Sword_Art_Online_1080p_Dual_Anime_x_Clan_fbfef207/candidate_review_06d/`

Open `index.html` in a browser. Export the JSON after annotation. The selected review set is intentionally not 36 rendered Shorts; it contains only short muted previews and sampled stills.

## Evaluation metrics

Run `python -m evaluation.anime_review <anime_highlight_dataset.json>` to calculate metrics. With fewer than five annotation records, it returns `INSUFFICIENT_DATA` and `metrics: null`. Once enough human labels exist, it calculates:

- Precision at K among the highest-scoring **reviewed** candidates (K up to 5), and keep rate among reviewed final selections.
- Category agreement only where a non-Unclassified prediction and human category both exist.
- Temporal IoU and mean boundary error only for human-corrected intervals.
- Pairwise overlap rate among selected predictions and six-region coverage.
- Hook, context, payoff and caption acceptance (human ratings of 4 or 5 on kept annotations).

The metric definitions are deliberately explicit about their denominator. Precision@K is currently among reviewed candidates, not all 2,461 manifest candidates, because the remaining candidates have no human labels. These values cannot be interpreted as full-episode recall or an unbiased estimate until the review protocol labels a representative set.

## Visual-analysis provider contract

`evaluation/providers.py` defines a provider-neutral sampled-frame request and normalized event result. The result retains raw provider output separately from normalized description, entities, actions, visual changes, emotion cues, category, confidence, evidence timestamps and uncertainty. The deterministic mock labels itself `deterministic-mock`, states that it did not analyze pixels, and is used only by tests. It is not connected to the production selector.

## Local model feasibility

### SmolVLM2-500M-Video-Instruct

Current local inspection:

| Check | Observed |
| --- | --- |
| Python | 3.14.7, 64-bit |
| OS / RAM | Windows 11 Home, 15.71 GiB visible RAM |
| GPU | NVIDIA GeForce RTX 3050 A Laptop GPU, 4,094 MiB dedicated VRAM; 2,653 MiB free at inspection |
| CTranslate2 | 4.9.0; reports one CUDA device and supported compute types |
| PyTorch / Transformers / Accelerate / Pillow | Not installed in active `.venv` |
| Pinned SmolVLM2 snapshot | Required files not present in the HF cache |
| Local LLM endpoint | Not configured (`CLIPPER_LOCAL_LLM_ENDPOINT` empty) |
| Real SmolVLM2 experiment | **NOT RUN**; required dependencies and weights are absent |

The existing app's optional vision requirements are `transformers>=4.49,<5`, `accelerate>=1.2,<2`, `Pillow>=10,<13`, and `huggingface_hub>=0.32,<3`; PyTorch must be selected separately for Windows/CUDA. The current adapter extracts individual timestamped frames with FFmpeg, so it does not need the model card's `decord` video-reader path. Python 3.14 compatibility of this exact package combination has not been tested in this venv.

The pinned downloader's metadata-only dry run for revision `7b375e1b73b11138ff12fe22c8f2822d8fe03467` resolved 12 files totaling **2,034,845,165 bytes (about 1.89 GiB)** under its existing allow-list. The existing installer reserves an extra 512 MiB for cache overhead, so its preflight would require about **2.39 GiB free disk**. The current SmolVLM2 model card describes the model as 500M parameters, Apache-2.0 and claims 1.8 GB GPU RAM for video inference; that is a published claim, not a measurement on this laptop. Current available VRAM was only about 2.59 GiB at inspection, leaving little headroom if that estimate applies. No peak RAM/VRAM, inference time or description quality can be reported without running the approved real model experiment.

The existing approach processes short sampled sequences (up to 12 FFmpeg-extracted 512px images per candidate), rather than sending a whole episode to the model. For a future approved feasibility run, first install the listed packages plus a matching official Windows PyTorch/CUDA build, download only the pinned 12-file model set, confirm the full media cache size, then test no more than five representative sequences and record device, peak VRAM/RAM, latency, response and human visual agreement. This task did none of those installs or weight downloads.

### Moondream alternative

The official Photon docs now list Windows AMD64, Python 3.10–3.14 and Ampere-or-newer NVIDIA GPUs, so this RTX 3050 and Python version satisfy the documented platform family. The documented Moondream 3.1 Photon model is `moondream3.1-9B-A2B`; the official overview describes 9B total / 2B active parameters, but the reviewed local-inference page does not provide an RTX 3050 VRAM figure. The package downloads weights at first use. No Photon package or weights are installed. Moondream remains an alternative to benchmark after the human review set exists; it is not confirmed to fit 4 GiB VRAM.

### Sources

- [SmolVLM2 official model card](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct) and [pinned repository file list](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct/tree/7b375e1b73b11138ff12fe22c8f2822d8fe03467)
- [Moondream local inference and Photon documentation](https://docs.moondream.ai/running-locally/) and [official overview](https://docs.moondream.ai/)

## Validation

| Check | Result |
| --- | --- |
| New Task 06D unit tests | **5 passed** |
| Full test suite: `.venv\Scripts\python.exe -m unittest discover -s tests` | **120 passed, 0 failed** |
| Candidate review generation from cached episode artifacts | **36 cards generated**; all 36 have three stills and a muted 640×360 H.264 12fps preview; no full-episode processing |
| Dataset annotation save/load and invalid-rating rejection | **Passed** |
| Fewer-than-five-label metric gate | **Passed**; initial dataset remains insufficient by design |
| Deterministic mock provider contract | **Passed** |
| Python compile check: `.venv\Scripts\python.exe -m compileall -q evaluation tests` | **Passed** |
| Review board JavaScript syntax check (Node 24.19.0) | **Passed** |
| `git diff --check` | **Passed** |
| Model weights or package install | **None** |

## Files and limitations

New tracked files: `evaluation/anime_review.py`, `evaluation/build_review_board.py`, `evaluation/review_board.html`, `evaluation/anime_highlight_dataset.schema.json`, `evaluation/providers.py`, and `tests/test_task06d.py`. This report and `reports/IMPLEMENTATION_STATUS.md` document the milestone. Existing `golden_set.py`, `storyboard.py`, and the production selector are unchanged.

There are no human annotations yet, so no benchmark quality values exist. Browser local storage is scoped to the browser's local-file origin; reviewers should export the JSON dataset regularly and use that exported file as the portable record. The candidate set and automatic strata are for human review, not ground-truth classes. GPU capability detection in CTranslate2 does not prove PyTorch/SmolVLM2 can load or run on CUDA.

Task 06E and Task 07 have not started. The feature-branch commit and remote status are recorded below after publication.

## Git publication

- Implementation commit: `4c6d2b911022cb50d4b479679d1fdfaf47464407`
- Feature branch: `feature/task-06d-multimodal-evaluation`, pushed to `origin` and verified at the implementation commit before this report-only update.
- Protected remote branches unchanged: `main` = `1fe43b76de7c20930c34f36c6ea86696fd72c695`; `backup/task-06b-stable-20261112` = `37e6bcbcb7f9c220a265c2215fdf2709ea2323f0`.
- No changes were pushed to `main` or the Task 06B backup.
