# Clipper Personal Production Workflow

## Decision

Build Clipper as a local, single-episode anime-to-Shorts workflow. The daily path should become:

**Choose one episode → start generation → review local MP4/SRT drafts.**

The first usable release can reuse Clipper's current single-episode pipeline, but its candidates must be labeled **provisional**. Existing selection and Qwen output have not passed human editorial evaluation. A production-quality claim or automatic `Final` output is reserved for a later quality gate.

This specification separates the user-facing workflow from the research needed to improve highlight quality. It does not authorize model/package downloads or publishing integration.

## Baseline inspected

- Specification branch: `docs/personal-production-workflow-spec`
- Base commit: Task 06I branch tip `670f94c0360430efe43ba5896e793a0915f11be7`
- Production UI: `main.py`, `ClippingApp`, already supports choosing one episode, inspecting streams, starting a single-episode job, progress, cancellation, output opening, and a separate nightly queue.
- Single-episode pipeline: `clipper.py:process_video`; it has local transcription, scene/motion/audio signals, existing analysis/transcript/editorial/semantic caches, candidate selection, rendering, and output validation.
- The current GUI exposes both one-episode and nightly workflows alongside many settings. The default clip count is five; single-episode mode accepts up to five.
- `analysis_cache.json` reuses motion, audio-energy and scene-cut results. A single versioned episode timeline joining those signals, dialogue, frames, and event proposals does not yet exist.
- The renderer produces portrait MP4/SRT outputs but does not implement a saved channel-branding profile, logo overlay, anime title overlay, or subscribe CTA.
- Task 06I provides the separate human review workspace. Its current session has no human annotations; the full episode review remains pending.
- `feature/task-06d3-ui-redesign` is local and divergent. Reuse is by inspection and selective port only; do not merge or cherry-pick it wholesale.

## Product boundaries

### In scope for Personal Preview

- One local video episode per job.
- Offline processing and local output only.
- Automatic candidate discovery and rendering using the current pipeline, with up to two draft clips.
- English caption output where supported by the selected source audio and configured transcription/translation mode.
- Existing blurred-background portrait presentation and original audio.
- A short processing-stage/status view, cancellation, and a direct way to open the job output.
- Retained source media, resumable cached analysis, and validated MP4/SRT output.
- A visible provisional label and a concise explanation that current editorial selection is not yet validated.

### Explicitly out of scope

- YouTube login, upload, scheduling, or cloud APIs.
- Folder/batch/nightly processing in the daily workflow.
- A full timeline editor, multi-track editing, transitions, or effects library.
- Treating Qwen descriptions, heuristic scores, or old production selections as ground truth.
- Calling existing provisional outputs quality-approved or guaranteed to be interesting.
- Replacing the current application architecture with another repository.
- New model or runtime installation without a separately approved feasibility plan.

## Ordered implementation milestones

Each milestone should be implemented and accepted separately. The next coding task is **Personal Preview 0.1** only.

| Milestone | Deliverable | Depends on | Acceptance gate |
| --- | --- | --- | --- |
| **Personal Preview 0.1 — daily single-episode flow** | A simple landing screen that selects one episode, confirms ambiguous audio, starts the existing pipeline, reports progress, and opens local draft results. | Existing `ClippingApp` and `process_video`. | A fresh launch does no work; one explicit Start processes only the chosen file; cancel is safe; at most two provisional MP4/SRT drafts are written; originals and existing outputs are preserved. |
| **Personal Preview 0.2 — shared episode timeline** | Versioned episode analysis artifact shared by scene, motion, audio, transcription, sampled-frame, and event-proposal stages. | 0.1 output lifecycle; existing cache formats audited. | A fixture episode produces the same timeline deterministically; changing only render settings reuses analysis; changing the source or a signal-stage version invalidates only affected cache entries; a failed job resumes at the first missing stage. |
| **Personal Preview 0.3 — saved branding profile** | Local profile for channel logo, anime title, optional CTA, and safe-area placement, applied during rendering. | Renderer and 0.2 stage/cache boundaries. | Branding changes rerender only; disabling the profile reproduces current rendering; logo/title/CTA remain inside portrait safe areas; original video/audio remain intact; output passes existing technical validation. |
| **Personal V1 — validated automatic selection** | Anime-aware event discovery, evidence verification, complete-event boundaries, competitive ranking, and quality-based abstention. | A human-reviewed Task 06I dataset and later evaluation milestones. | Against held-out human-reviewed episodes, each selected moment passes defined editorial and evidence checks; duplicate/unsupported/incomplete moments are rejected; zero outputs are allowed. Acceptance thresholds must be defined before measuring. |

The existing research numbering remains unchanged: Task 06J is Qwen reliability, followed by adaptive perception and dialogue/event work. The preview milestones are product work, not a renumbering of Tasks 06J–06P.

## Personal Preview 0.1 implementation specification

### Main screen

Show only the controls needed to make one local job understandable:

1. **Choose Episode** — standard Windows file picker for one supported media file.
2. **Episode and audio** — filename, duration, resolution, and available audio tracks. If language metadata is ambiguous, require explicit track selection. Never choose a track merely because it is marked default when another track is a better language match.
3. **Generate Draft Shorts** — the only processing entry point. Processing starts only on this explicit click.
4. **Progress** — current stage, actual transcription device, progress/log summary, and safe Cancel.
5. **Results** — number produced, provisional status, output folder action, and links to MP4/SRT artifacts.

Place low-frequency choices under a collapsed **Advanced settings** area. Keep useful defaults and avoid requiring config-file edits. Do not show nightly queue controls on the daily screen; retain existing queue functionality outside this workflow rather than deleting it.

### Processing behavior

- Route work through the existing single-episode `process_video` path. Do not fork or duplicate candidate/render logic for the new screen.
- Default to at most two clips. A run may produce one or zero if the current pipeline finds fewer valid candidates.
- Label results **Drafts — provisional selection** until Personal V1 passes editorial acceptance.
- Use existing caches where their source fingerprints and settings remain valid. Report cache reuse honestly; do not promise a stage-level resume that the existing implementation does not provide.
- Preserve one GPU-heavy model stage at a time and release model resources between stages. Show the actual Whisper device/fallback status rather than inferring it from GPU presence.
- Keep the CPU fallback, model-cached-only behavior, no-download normal workflow, explicit cancellation, and safe retention of completed outputs.
- Store each job in an isolated output directory with `analysis.json`, MP4 and SRT artifacts. Include stage outcomes, source identity, selected stream, and provisional status in the report.
- Never modify source media. Do not silently overwrite an earlier job; use a unique job directory or explicit, validated reprocess action.

### Human-review relationship

Personal Preview 0.1 may proceed before the user completes Task 06I, because it does not change or claim to validate editorial intelligence. Keep the blind review workspace and its data isolated. Do not show its human annotations during Pass 1. Do not use its results in automatic selection until a later task explicitly defines training/evaluation use and preserves the separation between reference labels and predictions.

Task 06I remains required for accepting Personal V1. The benchmark is a one-time calibration for this episode and reviewer, not a claim of broad accuracy or inter-reviewer agreement.

## UI redesign porting rule

Before Personal Preview 0.1 changes `main.py`, compare the current Task 06I base with local `feature/task-06d3-ui-redesign` and record the UI elements worth reusing. Port selected presentation/layout changes into the new implementation branch. Do not cherry-pick or merge the diverged branch, and do not bring its unrelated behavior or configuration along without review.

## Personal Preview 0.1 acceptance checklist

- [ ] Implementation branch is created from the verified Task 06I tip; protected branches and Task 06B backup stay unchanged.
- [ ] Fresh launch restores harmless preferences only and starts no analysis, transcription, queue, or rendering.
- [ ] One file can be chosen without scanning a directory or enrolling other episodes.
- [ ] Audio tracks and caption mode are clearly shown; ambiguous audio requires the user's choice.
- [ ] Only **Generate Draft Shorts** starts work.
- [ ] At most two local drafts are requested; fewer, including zero, are valid outcomes.
- [ ] Output is explicitly labeled provisional and contains MP4, SRT, and a truthful analysis report.
- [ ] Progress remains responsive; cancellation preserves completed artifacts and records an interrupted job.
- [ ] Reopening does not automatically resume work; the user explicitly starts a new run or confirms any supported recovery action.
- [ ] Existing cache behavior is reused and accurately described; no full episode analysis is rerun merely to open the UI.
- [ ] No network upload, cloud API, new model/package download, webcam, screenshot, or desktop capture is added.
- [ ] Focused tests cover explicit-start safety, single-source isolation, audio mapping, cancellation/retained outputs, provisional metadata, and output-folder behavior.
- [ ] Full project tests, compilation, and `git diff --check` pass before publication.

## Risks and unresolved decisions

- Current selection quality is not accepted. The UI must not imply that two outputs are guaranteed to be good; `Drafts` and provisional status are mandatory.
- The current GUI starts single-episode jobs with a default of five clips and currently mixes this path with the nightly queue. The 0.1 workflow must set and validate the two-clip cap without silently changing queue behavior.
- Existing analysis caches are separate JSON artifacts; the shared timeline milestone needs explicit versioning, invalidation, and resume semantics before code is consolidated.
- Branding can force a new render and may increase encode time. It must be an independently cached render stage so logo/title changes do not trigger transcription or visual inference.
- Track language metadata can be missing or wrong; selected audio should be observable in the job report and manual acceptance must include listening.
- The current Whisper CUDA runtime has previously failed and fallen back to CPU. Personal Preview must show the actual device and retain the tested fallback.
- The actual selected MKV's English audio synchronization has not yet been independently listened to and accepted under Task 06I.

## Recommended next coding task

Implement **Personal Preview 0.1 — Daily Single-Episode Workflow** from the Task 06I baseline. Keep the change limited to the simplified daily UI, explicit one-episode job lifecycle, a two-draft provisional output contract, and focused safety/output tests. Do not include the shared timeline refactor, branding renderer, Qwen changes, or new model dependencies in that first code task.
