# Universal recorded-video support — 2026-11-14

Branch: `codex/universal-recordings`, based on verified local/remote `feature/personal-v1-playbook` tip `37cd8f379326c781bea8d939c979a1470c217577`. Other branches and untracked `vlc-help.txt` preserved. Scope: both news/interview/commentary and sports/gameplay recordings, alongside films and animation. Local files only.

## Reference-code findings and decisions

Inspected pinned source, not just README claims; downloaded selected source text into ignored `.runtime/reference-repos/`, without running it or installing dependencies.

| Reference | Useful implementation | Clipper decision |
| --- | --- | --- |
| [OpenShorts `8af4a48`](https://github.com/mutonby/openshorts/tree/8af4a48a2bdda96c9a02bb8df3f09d4e7ec99e0f) | `layout_picker.py` routes screen content differently from talking heads; explicit choices win. `hook_grounding.py` notes transcript-only hooks miss on-screen meaning. `clip_selection.py` separates windows, shortlist and word-boundary adjustment. | Added explicit source profiles using Clipper's existing renderer. Preserve full source for broadcasts, gameplay and presentations. Retained existing cached analysis, word timings and manual hooks. No Gemini layout/hook calls or unvalidated automatic claims. |
| [AI-Youtube-Shorts-Generator `1142a74`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/tree/1142a747b0056da5551970e88ca9f1ec85deafc5) | `highlights.py` chunks long transcripts with overlap and removes lower-score overlapping nominations. `local/clipper.py` uses a separate cut then face-crop stage. `local/llm.py` calls OpenAI/Gemini despite the local-mode name. | Strengthened shared deduplication to catch nested intervals: existing IoU >0.35 OR intersection >80% of the shorter interval, symmetrically. Both dedup and diversity use the rule. Kept Clipper's existing render path, local models and caches; no cloud API or redundant encoding stage. |

Both inspected core LICENSE files are MIT. OpenShorts NOTICE explicitly excludes `cloud/`, which has a separate commercial license. No third-party implementation was copied or vendored; the changes independently reuse Clipper helpers. Reference-reported model accuracy and speed were not reproduced here.

## Delivered behavior

- Browser: General, Animation, Broadcast, Sports/gameplay, Presentation. Selection and polling remain idle. Automatic and manual requests validate and persist the chosen profile; old manifests still load. UI uses recording terminology.
- Animation keeps existing automatic episode zones. Other browser profiles exclude only owner-entered/edited zones, so `Opening remarks`, late news segments and gameplay endings are not automatically treated as OP/ED. Raw detections, overrides and extraction caches remain intact; job snapshots list inactive detections separately. Legacy/research callers omitting the profile retain their previous policy.
- Full-frame profiles use one contain/blur layout through automatic rendering, manual rendering and subsequent rerenders. No face-model download, subject identity claim, new score or semantic selector. Dedup detects time overlap, not visual replays at unrelated times.

## Actual verification

- Starting baseline: **326 passed + 9 subtests**, 0 failed/skipped, 48.83 s. Final full suite: **338 passed + 9 subtests**, 0 failed/skipped, 47.71 s (`python -m pytest -o faulthandler_timeout=120 -q`). Twelve new tests cover HTTP profile validation/forwarding, raw-cache preservation, owner overrides, nested duplicates in both score orders, cancellation, and a synthetic MP4 render/rerender validated at **1080×1920** with full-frame composition. Existing suite covers codecs/audio/subtitle validation.
- **15 Playwright checks passed** in headless Edge against disposable loopback port 8775: source-profile choice survives polling, file chooser, idle startup/import, edit preservation, saved-job reload, desktop/mobile layout; zero processing requests or page/console errors. Evidence: ignored `output/playwright/result.json`, desktop/mobile screenshots. This was headless browser testing, not an in-app-browser listening test.
- Compilation and diff checks passed. All **18** previously recorded source/cache/model/job/reference/raw-result hashes and all **14** supplied playbook file hashes remain unchanged. No full episode analysis, AI inference, model/package installation, capture or media download.

Next: evaluate separate owner-reviewed examples for news/interviews and sports/gameplay before introducing a local semantic ranker or active-speaker/split-screen layout. Keep the existing failed 2B editorial gate closed. No real broadcast sample was provided: broadcast editorial quality, small-text readability, audible language and perceptual synchronization remain unverified. Use `run.bat` after restarting your existing server when convenient; choose Animation for anime OP/ED filtering. Remote delivery is verified separately against the pushed branch SHA.
