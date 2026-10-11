# Testing

After setup, `bash scripts/test.sh` runs lint/format checks, stream assembly and
timestamp tests, cache/crop equivalence, and native queue
and latency-histogram tests. These tests do not open audio devices.

A real worker test verifies 44.1 → 48 kHz conversion, stereo Bypass, finite mono
conversion, and generation resets. Supply your own permitted voice reference:

```sh
.venv/bin/python tests/worker_protocol.py --reference /absolute/path/reference.wav
```

For an actual app route, build the app and dedicated quiet test player, then
choose an output UID from the inventory. This plays audio on that device.

```sh
bash native/build_app.sh
bash scripts/build_test_player.sh
native/build/VoiceRelay.app/Contents/MacOS/VoiceRelay --inventory
.venv/bin/python tests/live_smoke.py \
  --output-uid 'DEVICE_UID' \
  --source /absolute/path/source.wav --reference /absolute/path/reference.wav \
  --seconds 90 --switches 10,14,32,36,54,58,76,80 \
  --max-p95-ms 620 --report .local/tests/live.json
```

The test uses only its own source player and requires zero queue overflows and
playback underruns, completed Bypass transitions, and the requested conversion
p95 threshold. Reported delay is capture timestamp to scheduled Core Audio
output, not acoustic/headphone latency. A finite run does not guarantee stability
under every workload. Logs and reports stay in ignored `.local/tests/`.

GitHub Actions runs the automated checks and native compile on a macOS runner.
It downloads only pinned source and small metadata files with
`python scripts/setup_runtime.py --code-only`; no model weights or recordings
are needed for CI. Actual MPS/MLX inference and audio-device tests remain local.
