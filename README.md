# VoiceRelay

Convert an app's spoken audio into another voice, locally on your Mac. Select
an app, a voice reference, and an output device; press **Start**. **Bypass** plays
the original voice. Your microphone remains connected directly to the source app.

VoiceRelay uses X-VC with full 2.4-second context, acoustic caching, and an MLX
decoder. The current routing target is **600 ms**, excluding Bluetooth and
acoustic delay. This is an early local build, not a signed standalone installer.

## Requirements

- Apple Silicon Mac running macOS 14.2 or later.
- Apple Command Line Tools: `xcode-select --install`.
- [uv](https://docs.astral.sh/uv/): `brew install uv`.
- About 10 GB of free disk space for dependencies and model assets.
- A clean 1–120 second WAV, FLAC, or AIFF reference; its first three seconds set
  the voice. Use audio you have permission to use.

## Build and run

```sh
git clone https://github.com/Tailen/voice-relay.git
cd voice-relay
bash scripts/setup.sh
bash native/build_app.sh
open native/build/VoiceRelay.app
```

Setup creates `.venv` using the locked Python dependencies and downloads about
6.5 GB of pinned, checksum-verified model assets into `.runtime`. It can be rerun
after an interrupted download. Conversion itself runs offline.

1. Open the source app and select it in VoiceRelay. **Refresh** updates the list.
2. Choose your voice reference. Leave **Current output** selected or pick a device.
3. Press **Start**. Allow macOS's system-audio recording permission if prompted.
   First use loads and warms the model; this can take tens of seconds.
4. Use **Bypass** to hear the original audio. Press **Stop** to restore normal
   source-app playback and release the model.

Keep the checkout, `.venv`, and `.runtime` together. Rebuild after moving the
checkout or updating the code: the local app embeds this checkout's paths.

## Limits and troubleshooting

- Headphones avoid feedback. Bluetooth adds delay beyond the reported routing
  latency; latency and headroom depend on your Mac and other workloads.
- Only the selected app and its related audio processes are captured. Conversion
  is continuous; the source app retains responsibility for interruptions.
- Bypass changes briefly rebuffer. Conversion outputs mono to both channels;
  Bypass preserves stereo. Mono/stereo float PCM output devices are supported.
- If the source app exits, the device changes, or audio underruns, the route
  stops. Select the device again and press **Start**. Automatic reconnect and
  sleep recovery are not implemented.
- If capture is denied, enable VoiceRelay under **System Settings → Privacy &
  Security → Screen & System Audio Recording**, then restart it. The exact
  settings label varies by macOS version. No microphone permission is needed.
- For startup errors, run the executable in a terminal to see worker diagnostics:
  `native/build/VoiceRelay.app/Contents/MacOS/VoiceRelay`.

## Development

```sh
bash scripts/test.sh       # lint, stream/cache/crop tests, native ring/timing tests
bash native/build_app.sh   # compile and verify the locally signed app
```

See [architecture](docs/architecture.md), [test instructions](docs/testing.md),
and [validation results](docs/validation.md). The repository contains app code,
tests, and asset manifests. Model weights, recordings, generated applications,
and the earlier local research archive are intentionally excluded.

## License and credits

VoiceRelay's code is [MIT licensed](LICENSE). It uses
[Jerrister/X-VC](https://github.com/Jerrister/X-VC), GLM-4-Voice-Tokenizer, and
ERes2Net; their source, model weights, and terms remain separate. See
[third-party notices](THIRD_PARTY_NOTICES.md).
