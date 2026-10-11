# Validation — 12 October 2026

Tested on the development Apple Silicon Mac with AirPods Pro 3. These are
bounded local tests, not guarantees for every Mac, source app or workload.

| Check | Result |
| --- | --- |
| Clean checkout | Locked `uv sync`, pinned source/metadata downloads, full asset verification, native build and signature verification passed |
| Automated tests | 10 Python tests; native ring concurrency/generation and timing-histogram tests passed |
| Worker protocol | 44.1 kHz input → 48 kHz output, stereo Bypass against SoXR, finite dual-mono conversion, generation resets passed |
| Runtime equivalence | Eight speech windows matched the previous optimized runtime exactly; maximum absolute output error 0 |
| Final native live test | 60 seconds, three Bypass/resume cycles, 993 output packets; zero underruns, zero overflows, no fault |
| Final conversion delay | 603.32 ms; p50/p95/p99 upper-bound histogram buckets all 604 ms |
| Earlier extracted-runtime test | 90 seconds, four Bypass/resume cycles; approximately 609.52 ms, zero underruns or overflows |
| Publication audit | No recordings, model weights, installed dependencies, generated binaries, credentials, or personal absolute paths staged |

The app still targets **600 ms**. Capture timestamps are compared with scheduled
Core Audio output timestamps; callback boundaries account for the few extra
milliseconds. Bluetooth transport, DAC and acoustic delay are excluded. Bypass
transitions intentionally clear and re-prime the route and briefly interrupt
output. They are separate from unexpected playback underruns.

The final live test's largest measured model block was 199.91 ms and largest
reply gap was 160.42 ms. Buffering absorbed those delays in that run. The full
2.4-second context, weights and precision were preserved. The waveform equality
check covers the extracted inference implementation; it is not a new subjective
listening evaluation.

The clean-checkout tests also passed without model weights installed, using
only the pinned source and small metadata files, as configured in GitHub Actions.
Hosted CI covers CPU/unit behavior and native compilation; physical audio and
MPS/MLX inference require a local Mac test. See [testing](testing.md) for commands.

Historical research, listening pages, fixtures, raw logs and detailed timing
reports remain in the original local workspace and are excluded from GitHub.
