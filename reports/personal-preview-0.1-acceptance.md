# Personal Preview 0.1 — Real-Media Acceptance

## Scope and environment

- Acceptance run: 2026-11-14, through the local browser console in Codex's in-app browser; no Tkinter window was opened.
- Source: `[S01-E01] Sword Art Online [1080p] [Dual] @Anime_x_Clan.mkv` (local path omitted from the repository report).
- FFprobe: 1422.590 s, 1920×1080 HEVC at 24000/1001 fps, 333,333,074 bytes.
- Audio: global stream 1 is Japanese AAC stereo; global stream 2 is English-tagged AAC stereo. The browser workflow explicitly selected global stream 2 and Whisper transcription.
- Current branch: `feature/personal-preview-0.1-daily-flow`, HEAD `493135dd26c03e9223664c2cab49783d172eb8a3`. Acceptance changes are local and uncommitted. No Personal Preview 0.2 branch was created. No source media was modified.

## RM-01 through RM-07

| Check | Result | Evidence and limits |
| --- | --- | --- |
| **RM-01 — Launch and File Selection Idle** | **PASS — no processing started** | The local review workflow opened without auto-starting work. Selecting the episode populated its path/track choices without starting a worker; the selected episode remained idle until the explicit Generate action. The full suite also verifies that launch restores state without starting processing. |
| **RM-02 — Explicit English Audio Selection** | **RM-02a PASS; RM-02b PENDING OWNER CONFIRMATION, not blocking** | RM-02a: the browser workflow explicitly selected FFprobe global audio stream 2 and passed stream index 2 to processing. FFprobe metadata labels it `eng`, and cached Whisper language detection returned `en`. RM-02b: audible identity remains pending owner listening confirmation; this does not block the owner's technical acceptance decision. |
| **RM-03 — First-Run Processing and Output Generation** | **PASS — technical** | Job `20261114-090901-fa64a2e5` completed two validated MP4/SRT pairs. Both MP4s opened in the browser; clip 1 visibly played and both showed the intended portrait layout with the complete source image over a blurred background. FFprobe: clip 1 39.400 s; clip 2 45.329 s; both MP4/H.264 1080×1920 30 fps, AAC 96 kHz stereo, audio/video stream start times 0.000 s. The clips remain provisional. |
| **RM-04 — MP4 and SRT Verification** | **RM-04a technical PASS; RM-04b PENDING OWNER CONFIRMATION, not blocking; RM-04c known ASR errors** | RM-04a: source/output audio comparison at both candidate starts had best offset 0 ms and waveform correlations 0.9989 (clip 1) and 0.9990 (clip 2). Frame comparisons: correlation 0.99957 / MAE 1.51 (clip 1) and 0.99974 / MAE 1.40 (clip 2). RM-04b: perceptual A/V sync remains pending owner listening confirmation; this does not block the owner's technical acceptance decision. RM-04c: the formatter and grouping fixes are validated; proper-noun errors are confirmed (`Kayaba`, `Klein`) and will be mitigated in this sprint by the per-project glossary and simple editor. |
| **RM-05 — Repeat-Run Cache Reuse** | **PASS** | Same source, stream 2 and Whisper transcription in job `20261114-093308-fffa9edb`. The saved report records `analysis_cache=reused` and `transcription_cache=reused`; it completed two drafts with 2,162 cached words. The repeated MP4 and SRT files are byte-for-byte identical to the first batch. |
| **RM-06 — Mid-Run Cancellation and Preservation** | **PASS — preservation; status display corrected** | Job `20261114-094151-062af89c` was cancelled while clip 2 rendered, after clip 1 validated. Its interrupted manifest retains `clip_001.mp4` and `clip_001.srt`; clip 2 is not reported as complete. Both earlier completed job folders remain intact. SHA-256 hashes for the analysis and transcription cache files were unchanged across the repeat and cancellation runs. The browser initially displayed `0 draft(s)` despite one persisted completed draft. The status count now uses persisted completed drafts; its regression test passes. |
| **RM-07 — Technical Acceptance Separate from Editorial Quality** | **PASS — separation documented** | Technical validation is recorded above. The selector labeled both drafts `activity-only`; no human reviewed whether they are good or publishable Shorts. Editorial quality is **NOT EVALUATED**, and no “ready to post” claim is made. Whisper used CPU int8 after CUDA initialization failed with `cublas64_12.dll` missing; this runtime fallback is separate from editorial quality. NVENC rendered both videos after its self-test passed. |

## Artifact evidence

The two completed jobs produced identical outputs:

| Artifact | SHA-256 |
| --- | --- |
| `clip_001.mp4` | `DD16AE4D35E638A471B66B91086B35AF55330D5E98AC56230ECB1AFEE8CB6882` |
| `clip_001.srt` | `643FA1AE799723AF0E0C2EA258F04BCC476F823CC32208996E92D2E1C5F1952D` |
| `clip_002.mp4` | `E633E738439A0D069A32ED7B8D6B17224BCFC896D8A86FCA2827FC06563F2BC4` |
| `clip_002.srt` | `191CDC23F51A9B92A9E92A25AB6D9012CEE8B11561AF7D2267E559211AAC6E32` |

Cache hashes after the first run, repeat run, and cancellation run:

- `analysis_cache.json`: `4203E5561C89368497226200B3FD6E2CE726892D3E32BB1E4C22F8FCFAD06175`
- `transcription.json`: `3BBF6615113EC4842FC93013B85B5531E986C33B7647406117ABA2CF18EBEC19`

Job folders:

- First batch: `Shorts_Output/jobs/20261114-090901-fa64a2e5`
- Cache repeat: `Shorts_Output/jobs/20261114-093308-fffa9edb`
- Cancelled run: `Shorts_Output/jobs/20261114-094151-062af89c`

## Automated verification

- The exact previously stalled test, `tests/test_personal_preview.py::PersonalPreviewTests::test_file_selection_is_idle_and_ambiguous_default_does_not_choose_audio`: **1 passed** in 0.54 s.
- Full Personal Preview test module: **6 passed** in 1.54 s.
- Caption spacing/grouping focused tests: **3 passed** in 0.32 s.
- Full project suite after caption changes with `faulthandler_timeout=120`: **210 passed, 9 subtests passed, 0 failures** in 53.03 s.
- Focused browser/cache tests after the cancellation-status correction: **4 passed** in 1.68 s.
- Python compilation and `git diff --check`: rerun after report changes.

### Test-stall diagnosis

`_start_single()` combined `self.running` with invalid-file validation, and both cases displayed `messagebox.showinfo`. The test deliberately invoked Start a second time after the first call had set `self.running = True`; because the message box was not mocked, this opened a modal dialog against a withdrawn test window and blocked the suite. The minimal fix returns immediately when `self.running` is true, before validating the source. The test now asserts that the repeat call starts no second worker and displays no message box. No Tk lifecycle change was needed.

### Read-only Whisper token and SRT formatter diagnosis

The existing persistent cache was inspected without modification at `%LOCALAPPDATA%\Clipper\analysis_cache\<source-key>\transcription.json`. It records `Systran/faster-whisper-small@faster-whisper-1.2.1`, language `en`, audio stream index `2`, device `cpu`, precision `int8`, outcome `CPU_FALLBACK`; the recorded CUDA fallback reason is missing/unloadable `cublas64_12.dll`. Around the user-identified name, exact cached token strings (Python `repr`) are:

| Source start | Source end | Raw cached token (`repr`) |
| ---: | ---: | --- |
| 1093.080 | 1093.560 | `' Akihiko'` |
| 1093.560 | 1093.900 | `' Kaya'` |
| 1093.900 | 1094.060 | `'-la'` |
| 1094.060 | 1094.500 | `' created'` |

Whisper returned separate `Kaya` and `-la` tokens. The old `_srt()` stripped each token and forced a space between them, creating the extra space in `Kaya -la`. The cache does not contain `Kayaba`, so the misspelled name itself is still an ASR recognition/tokenization error. The old `_ass_text()` also joined tokens independently, so both rendered captions and SRT could show the spacing defect. The corrected implementation uses one token-aware join helper for SRT and ASS, preserving Whisper's provided inter-token whitespace. This evidence does **not** establish that CPU int8 caused the name error; that would require a controlled same-model/audio compute comparison, which was not run.

`group_words()` now moves the last word of a group into a following single-word cue only when the words are contiguous (gap at most 250 ms), no sentence boundary is crossed, and the result stays within the 36-character display limit. The moved token retains its original timing. Paused standalone cues such as `Congratulations.` remain separate. Regression tests cover spacing in both subtitle paths, SRT/ASS cue parity, trailing-word rebalance, and pause preservation.

## Caption-format validation render

The corrected formatters were exercised by rendering both original selected intervals into a separate folder, using the persistent transcription cache and the first-run clip boundaries/reframe plans. No transcription or model inference ran. The original acceptance job files were not overwritten.

- Validation job: `Shorts_Output/jobs/caption-format-validation-20261114-111841`
- Cached model: `Systran/faster-whisper-small@faster-whisper-1.2.1`; device `cpu`, precision `int8`; outcome `CPU_FALLBACK`.
- Cache hashes before/after remained identical: `analysis_cache.json` `4203e5561c89368497226200b3fd6e2ce726892d3e32bb1e4c22f8fcfad06175`; `transcription.json` `3bbf6615113ec4842fc93013b85b5531e986c33b7647406117aba2cf18ebec19`.
- All four files in the original first-run job retained their recorded SHA-256 hashes.
- New clip 1: 39.400 s, 1080×1920 H.264/AAC, 10 SRT cues; MP4 SHA-256 `6987f49e1277c347932fca8dca5aa9e72dbb85cfafba1596dfc3d38a7673deb6`.
- New clip 2: 45.329 s, 1080×1920 H.264/AAC, 21 SRT cues; MP4 SHA-256 `23023214fea4a104fc5899bb5fb8259ff1c8ba9a405f2e7f71a5e10f3bfd8ea8`.
- SRT and karaoke ASS visible text matched cue-for-cue across all 31 captions after removing ASS timing/position tags.

Rebalanced cue examples (caption boundaries move with the existing word timestamps; no word timing was extended):

| Clip | Before | Validation render |
| --- | --- | --- |
| 1 | `move, wait for a little` / `bit.` | `move, wait for a` / `little bit.` |
| 1 | `start to activate, drive it` / `home.` | `start to activate, drive` / `it home.` |
| 2 | `He designed the Nerve Gear` / `rig.` | `He designed the Nerve` / `Gear rig.` |
| 2 | `If I die in the` / `game,` | `If I die in` / `the game,` |
| 2 | `You have to let us` / `go!` | `You have to let` / `us go!` |

The complete cue appendix above preserves the original exported SRTs as historical evidence; the corrected SRT files are in the validation job folder.

## Exported SRT cue appendix

The following is transcribed from the original first-run exported SRT files, preserving cue numbers, times, and line breaks. No caption or render was regenerated.

### Clip 1 — `clip_001.srt` (10 cues)

```srt
1
00:00:00,000 --> 00:00:01,199
Here's an easier way
to

2
00:00:01,199 --> 00:00:01,959
look at it.

3
00:00:04,169 --> 00:00:05,549
Right after you start
your

4
00:00:05,549 --> 00:00:06,549
move, wait for a
little

5
00:00:06,549 --> 00:00:06,829
bit.

6
00:00:07,149 --> 00:00:08,090
When you feel the
skills

7
00:00:08,090 --> 00:00:09,469
start to activate, drive
it

8
00:00:09,469 --> 00:00:09,709
home.

9
00:00:10,669 --> 00:00:11,750
Drive it home?

10
00:00:38,890 --> 00:00:39,410
Congratulations.
```

### Clip 2 — `clip_002.srt` (21 cues)

```srt
1
00:00:00,739 --> 00:00:02,399
Akihiko Kaya -la created
a

2
00:00:02,399 --> 00:00:03,439
virtual world.

3
00:00:04,019 --> 00:00:05,219
He designed the Nerve
Gear

4
00:00:05,219 --> 00:00:05,579
rig.

5
00:00:06,159 --> 00:00:06,999
The man's a genius.

6
00:00:07,959 --> 00:00:08,859
I've been a fan
of

7
00:00:08,859 --> 00:00:10,079
his for a long
time,

8
00:00:10,299 --> 00:00:11,039
so I know.

9
00:00:12,099 --> 00:00:14,459
Everything he just said
is

10
00:00:14,459 --> 00:00:14,999
the truth.

11
00:00:16,139 --> 00:00:17,379
If I die in
the

12
00:00:17,379 --> 00:00:17,799
game,

13
00:00:18,479 --> 00:00:19,859
I'll die in real
life.

14
00:00:37,089 --> 00:00:38,349
You have to let
us

15
00:00:38,349 --> 00:00:38,589
go!

16
00:00:38,749 --> 00:00:39,029
Seriously?

17
00:00:39,629 --> 00:00:40,409
What do we ever
do

18
00:00:40,409 --> 00:00:40,749
to you?

19
00:00:40,889 --> 00:00:41,669
We just want to
play

20
00:00:41,669 --> 00:00:42,089
a game!

21
00:00:42,449 --> 00:00:45,329
Come on, Kain.
```

## Acceptance gate

**Status: ACCEPTED by owner decision (technical baseline verified).** RM-01, RM-02a, RM-03, RM-04a, RM-05, RM-06 and RM-07 passed their stated technical checks. RM-02b audible English confirmation and RM-04b perceptual sync are **PENDING OWNER CONFIRMATION, not blocking**. RM-04c confirms ASR proper-noun errors (`Kayaba`, `Klein`); glossary and editor mitigation are in this sprint. Editorial quality remains unassessed. The original exported caption files remain unchanged. No Personal Preview 0.2 branch or implementation has been started.

## Local verification requested

Please play a short section of source global audio stream 2 and both rendered clips in the open in-app browser tabs. Confirm:

1. Stream 2 is the expected English audio.
2. Both clips sound synchronized with the visible action/dialogue.
3. Apart from the confirmed `Akihiko Kayaba` name correction, note any other SRT errors or missing lines.

After those checks, RM-02b and RM-04b can be updated. No 0.2 branch should be prepared unless the owner requests it.
