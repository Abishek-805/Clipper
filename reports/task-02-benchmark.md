# Task 02 Whisper hardware benchmark

- Model: `Systran/faster-whisper-small@faster-whisper-1.2.1`
- Model cached: **False**
- CUDA: **True** (1 device(s))
- CUDA compute types: `bfloat16, float16, float32, int8, int8_bfloat16, int8_float16, int8_float32`
- GPU memory before modes: `{'index': 0, 'name': 'NVIDIA GeForce RTX 3050 A Laptop GPU', 'total_mib': 4094, 'free_mib': 2476, 'used_mib': 1417}`
- Speech transcription benchmark: **NOT RUN**

| Mode | Result | Init seconds | First RTF | Warm RTF | Remarks |
| --- | --- | ---: | ---: | ---: | --- |
| cuda-int8_float16 | SKIPPED | — | — | — | Model is not in the local Hugging Face cache; benchmark does not download models |
| cuda-float16 | SKIPPED | — | — | — | Model is not in the local Hugging Face cache; benchmark does not download models |
| cpu-int8 | SKIPPED | — | — | — | Model is not in the local Hugging Face cache; benchmark does not download models |

No approved speech sample was provided. Transcription timing, language, segment count, RTF, and inference peak VRAM were not measured.

The model was not downloaded. Run a benchmark again after the user has explicitly installed the model.
