# Clipper commercial release assessment

The tested developer checkout can produce local clips. Shipping that checkout as a paid Windows product requires an installable, reproducible build and a decision about the rights granted to customers. The first release candidate should target a single-user Windows desktop, locally supplied footage, manual review and local export.

## Competitive workflows

This comparison uses public product documentation. Vendor claims about selection quality and editing speed require a common-footage benchmark before they can serve as product targets.

| Product | Documented workflow | Decision for Clipper |
| --- | --- | --- |
| [Descript](https://www.descript.com/tools/video-clip-maker) | Find clips and edit through text. | Link editable caption cues to the exact position in the draft player. |
| [Riverside](https://support.riverside.com/hc/en-us/articles/16175385028637-How-can-I-adjust-what-the-Magic-Clip-includes) | Restore surrounding conversation and adjust boundaries. | Retain source interval preview and manual boundary edits. Evaluate whether users can recover a missing setup sentence. |
| [OpusClip](https://www.opus.pro/clipanything) | Discover clips using visual, audio and sentiment cues across genres. | Measure dialogue and silent-event recall separately on human-marked recordings. |
| [Klap](https://klap.app/) | Reframe scenes according to layout, including gaming and screencasts. | Test face visibility, scoreboard retention and slide legibility as separate framing cases. |
| [CapCut](https://www.capcut.com/tools/long-video-to-shorts) and [Vizard](https://vizard.ai/) | Generate short drafts from longer recordings and offer editing tools. | Measure how quickly a first-time editor can reach a usable export and correct a poor suggestion. |

## RTX 3050 verification on Windows

The installed RTX 3050 A Laptop GPU reported 4,094 MiB. On a 30-second real excerpt encoded as H.264 High 10, NVDEC rejected the source profile. The original analysis then switched entirely to CPU. With the new path, software decoded the stream, CUDA resized the sampled frames and CPU measured motion and scene changes. A fresh automatic job ran Whisper on CUDA (2.432 seconds inference), encoded with NVENC, and validated a 22.1-second 1080×1920 H.264/AAC draft. The second job reused both analysis and transcription caches, skipped inference and encoded with NVENC again. These runs establish execution and format; they do not establish a speedup or editorial accuracy.

The original S01-E01 MKV took the full hardware path in a separate five-second analysis (40 sampled frames). Four-second interval previews used NVDEC plus NVENC on the original and CPU decoding plus NVENC on the High 10 excerpt. The editor displays the actual final encoder saved for each draft. Setup now fails with an actionable message when the export prerequisites checked by diagnostics are missing.

## Release gates

| Release gate | Evidence from this checkout | Evidence needed to close it |
| --- | --- | --- |
| Product terms | The tracked tree contains third-party notices covering historical research. It has no top-level product license. | Owner selects terms and verifies rights to distribute contributed code and assets. Include the selected terms in the actual release. |
| Reproducible installation | `setup.bat` upgrades pip and installs package ranges. The current environment contains NumPy 2.5.4, OpenCV wheel 4.14.0.94, PySceneDetect 0.7.3, faster-whisper 1.2.1, PyAV 18.1.0 and CTranslate2 4.9.0. | Build from a pinned, hashed dependency inventory, inventory native DLLs inside wheels, generate notices for the shipped payload and identify its checksum. Install and uninstall as a standard user on a clean supported Windows machine. |
| Media libraries | The external WinGet FFmpeg here identifies itself as Gyan 9.0.2 with `--enable-gpl --enable-version3 --enable-libx264`. No FFmpeg executable is tracked. PyAV wheels independently link FFmpeg and OpenCV ships a third-party notice in its wheel. | Decide which components customers install and which the vendor redistributes. Review the exact builds and codec licensing in target markets, with applicable notices and corresponding source for redistributed components where required. Review the Python wheels even if customers install the CLI separately. |
| Acceleration and optional models | The README documents app-local CUDA libraries in an ignored research-runtime path. Optional Whisper model download happens during setup. A clean installation cannot assume those files exist. | Test a fresh supported NVIDIA install and a CPU-only install. Inventory each supplied runtime and model revision, provenance, terms and fallback. Benchmark cold and cached jobs separately. |
| Customer acceptance | The documented activity selector yields provisional suggestions. The existing workflow supports manual intervals, review and local export. | Use rights-cleared recordings for a speech interview, fast action, animation and a recorded broadcast with multiple tracks. On each, verify first export, sound and sync, captions, cancellation, restart and re-export. Collect owner-rated clip usefulness separately. |

Record these results against the release checksum before advertising supported hardware, caption accuracy or automatic highlight quality. Publishing permission for a customer's recording is separate from the product's software license.

FFmpeg documents how compile-time options affect its license and identifies patent questions as jurisdiction-dependent. The upstream OpenCV wheel project and PyAV document their native dependencies. [FFmpeg legal guidance](https://ffmpeg.org/legal.html) · [OpenCV Python wheel licensing](https://github.com/opencv/opencv-python#licensing) · [PyAV binary wheels](https://pyav.org/docs/stable/overview/installation.html)

Review the license for each actual GPU runtime version. NVIDIA lists specific redistributable components in its toolkit supplement. The upstream Whisper project publishes model and code license information. [CUDA 12.8 supplement example](https://docs.nvidia.com/cuda/archive/12.8.0/pdf/EULA.pdf) · [OpenAI Whisper licensing](https://github.com/openai/whisper#license)
