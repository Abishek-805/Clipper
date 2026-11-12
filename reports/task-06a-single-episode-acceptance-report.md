# Task 06A — Single-episode baseline and acceptance

**Result: rendering passed; quality acceptance failed.** Task 06B contains the corrective implementation and rerun.

## Run evidence

- Source: `C:\Users\ashek\Downloads\Telegram Desktop\[S01-E01] Sword Art Online [1080p] [Dual] @Anime_x_Clan.mkv`
- Source media: 1,422.590 seconds, 1920×1080, HEVC, 23.976 FPS.
- Requested: five clips, 20–55 seconds; Whisper small; local semantic analysis disabled.
- Output: `Shorts_Output/_S01-E01_Sword_Art_Online_1080p_Dual_Anime_x_Clan_fbfef207/`.
- The run produced five 1080×1920 H.264/AAC MP4s with NVENC. Total measured pipeline time was 1,042.075 seconds.

## Acceptance failures

- All five SRT files were empty; manifest reported zero transcription words, `language: null`, and transcription failure.
- Faster-Whisper 1.2.1 passed the MKV path into PyAV 19.0.2, which raised `TypeError: open() got an unexpected keyword argument 'metadata_errors'` before inference.
- Audio analysis and final rendering both selected the first audio stream, Japanese global stream 1, despite the source having a default English AAC stream at global index 2.
- Semantic analysis was disabled and no local editorial model was configured. Every clip was labelled `mixed activity`; the contact sheet contained a character shot, bell, crowd, Japanese title card, and ending credits.
- Smart reframing fell back because OpenCV 5.0.0 has no `cv2.CascadeClassifier` attribute in this environment. The rendered portrait videos used a blurred background, but the fallback was not represented as a deliberate framing mode.

These results are not evidence of meaningful highlight selection or working transcription. The MP4 codec, geometry, and render pipeline were technically valid only.

## Corrective follow-up

Task 06B enumerated global streams, decoded selected audio through FFmpeg to an in-memory waveform, tested actual Whisper inference, selected English stream 2, enforced non-empty captions, and made blurred-background framing explicit. The one-episode rerun is recorded in [Task 06B report](task-06b-corrective-implementation-report.md).
