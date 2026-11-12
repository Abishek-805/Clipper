# Clipper GUI startup safety fix

## Objective

Ensure opening Clipper never starts video processing, restore the last selected folder for display, and require explicit user action before any new or resumed work begins. Record interrupted runs while retaining completed clips. Verify Clipper has no screen, desktop, webcam, or screenshot capture behavior.

## Implementation

- Persist the last folder and interrupted-job ledger under `%LOCALAPPDATA%\Clipper` (using the application directory as a non-Windows fallback).
- GUI startup restores the folder text and changes stale `running` jobs to `interrupted`; it does not call the worker or scan videos to process them.
- A startup prompt offers to prepare a valid interrupted job. Accepting only stages its folder and settings; the user must still click **Start Automatic Clipping** to begin.
- Closing during processing presents a confirmation, requests cooperative cancellation, and waits for the worker to finish its current cancellable operation before closing the GUI.
- The worker records completed source videos and keeps an interrupted-job record after cancellation or failure. Existing validated clip files are not removed on cancellation. A later confirmed resume skips completed source videos.
- Normal completion clears the interrupted-job record. Declining resume leaves it available for a later launch.
- Audited Python source for screen recording, screenshot/desktop capture, webcam/camera access, and related capture APIs. None are implemented or invoked by Clipper.

## Files changed

- `main.py` — folder preferences, interrupted-job persistence, confirmation and close handling, resume staging.
- `README.md` — startup, close, and resume behavior.
- `tests/test_startup_safety.py` — five GUI lifecycle and worker-ledger tests.
- `reports/IMPLEMENTATION_STATUS.md` — updated status and next-task state.
- `reports/startup-safety-fix-report.md` — this report.

No files were removed and no dependencies or model files were added.

## Verification

| Test | Command | Result | Remarks |
| --- | --- | --- | --- |
| Startup safety tests | `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_startup_safety.py" -v` | **PASSED — 5/5** | Runtime 0.493 seconds. GUI created on Windows; restored-folder startup did not call `process_video`; decline kept the job; accepting staged it until the Start button was invoked; close requested cancellation; worker recorded completed sources. |
| Full test suite | `.venv\Scripts\python.exe -m unittest discover -s tests -v` | **PASSED — 17/17, 0 failed, 0 skipped** | Includes the 12 Task 01 media, rendering, and pipeline tests. Runtime 16.994 seconds. |
| Capture API source audit | `rg -n "screenrecord|screen.?capture|screenshot|webcam|camera|Desktop Duplication|Windows.Graphics.Capture|mss|pyautogui|cv2\.VideoCapture\(0" --glob "*.py" .` | **PASSED** | No matching capture functionality was found in Clipper's Python source. |

The launch test constructs the actual Tkinter GUI with callbacks suppressed and asserts `process_video` remains uncalled. The resume tests verify a confirmation is required to stage a job and a separate Start button action is required before the worker is launched.

## Limitations and risks

- Cancellation waits for the current operation to reach its cancellation check. A long non-interruptible operation can delay window closure.
- Completed clips from the interrupted source remain on disk. Since that source's manifest is committed only after the source finishes, a user-approved resume can re-render clips already completed within that same source; each final clip is still promoted only after output validation.
- This fix did not benchmark Whisper, GPU memory, or long batch processing. The Task 01 report records those limits.
- No screen recording, screenshot capture, webcam access, or desktop capture feature is present in the app source.

## Git information

- Branch: `main`.
- HEAD: `acf5995f2630b19843d01e8c353d880d9211623e` (unchanged; changes are uncommitted).
- No commit or push was made. The working tree includes earlier uncommitted Task 01 work along with this fix.

## Next task

Task 02 remains **not started**. Wait for the user's instruction before beginning it.
