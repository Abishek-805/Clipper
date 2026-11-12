# Third-party notices

Task 05 did not copy or vendor third-party code, models, fonts, images, or media. The references below were inspected for architectural concepts and their license status; no copyright notice from those projects needs to be bundled for this change.

| Project | Source / version considered | License checked | Use in Clipper |
| --- | --- | --- | --- |
| AutoClip | `artbyjazi/autoclip`, main branch as inspected 2026-11-12 | MIT | Architecture reference only; no code/assets copied |
| Cutawan | `JeremySNR/cutawan`, main branch as inspected 2026-11-12 | MIT | Architecture reference only; no code/assets copied |
| OpenClipper | `GrepCut/OpenClipper`, master branch as inspected 2026-11-12 | MIT | Architecture reference only; no code/assets copied |
| MediaPipe AutoFlip | `google-ai-edge/mediapipe`, documentation | Apache-2.0 project license | Design reference only; no source integrated |
| SmolVLM2-500M-Video-Instruct | pinned revision in `vision.py` | Apache-2.0, per model card | Existing optional local model integration; weights are not included |

Links: [AutoClip license](https://github.com/artbyjazi/autoclip/blob/main/LICENSE), [Cutawan license](https://github.com/JeremySNR/cutawan/blob/main/LICENSE), [OpenClipper license](https://github.com/GrepCut/OpenClipper/blob/master/LICENSE), [MediaPipe license](https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE), [SmolVLM2 model card/license](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct).

## Existing runtime dependencies

This change did not add or upgrade a package. The repository's existing dependency ranges are in [requirements.txt](requirements.txt) and [requirements-vision.txt](requirements-vision.txt). Each dependency and the FFmpeg build used at runtime retains its own license and distribution terms; Clipper does not bundle FFmpeg or font files. The local machine's FFmpeg build/license and exact installed package inventory were not re-audited for this change.
