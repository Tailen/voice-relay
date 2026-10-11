# Architecture

```text
Selected app → private Core Audio process tap → bounded PCM queue
             → Python X-VC worker → bounded PCM queue → selected output
Microphone   → source app directly
```

`native/app` owns the AppKit UI, app-process selection, tap lifecycle, device
routing, buffering, timestamps, and fault recovery. `voicerelay` owns reference
conditioning, stream resampling, full-context inference, and conversion output.
The worker opens no audio devices and writes no captured audio to disk.

The worker uses 16 kHz mono, 120 ms hops, 100 ms future context, and 20 ms overlap
within a 2.4-second window. Acoustic caching preserves boundary calculations;
decoder cropping avoids output that would be discarded. The MLX decoder uses
the same FP32 weights. Model loading requires the full EMA state to match and
uses tensor-only checkpoint loading; no global PyTorch monkeypatch is needed.

At Start, the selected output is resolved once. The tap mutes ordinary source
playback while active. Worker preparation precedes capture. Output starts when
the first queued sample reaches the 600 ms capture-to-scheduled-output deadline,
with a minimum 40 ms queue. This avoids adding a fixed delay after inference.
Latency in the UI excludes Bluetooth transport, DAC and acoustic delay.

Generation tags isolate Bypass transitions. Old samples are discarded by the
consumer; rings are never cleared concurrently. Stop tears down the IOProcs,
tap, listeners and worker, restoring ordinary app playback. Device changes,
source exit, invalid data, pipe failures and buffer faults stop the route.

`voicerelay/assets.json` pins the upstream revision, download URLs, sizes and
SHA-256 digests. Setup verifies downloads before installation. `.runtime`
contains upstream source and model files; `.venv` contains locked dependencies.
Neither is published. The app is a local development build, tied to its checkout,
with no auto-update, reconnect, microphone capture, or turn-detection service.
