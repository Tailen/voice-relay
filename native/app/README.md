# AppKit and Core Audio

- `Main.mm`: source/reference/output selectors, Start/Stop, Bypass, session UI.
- `Session.hpp`: capture, bounded queues, worker IPC, output scheduling, teardown.
- `AudioSupport.hpp`: device formats, IOProc ownership, device-change listeners.
- `AudioRing.hpp`: one-producer/one-consumer PCM queue with generation tags.
- `Timing.hpp`: bounded latency instrumentation.
- `worker.py`: bundled entry point for the checkout's `voicerelay` package.

The 600 ms deadline counts elapsed capture and inference time. It requires at
least 40 ms queued output before starting; input/output queues remain bounded to
400 ms. Bypass retains a 40 ms startup wait. A mode change advances the generation
and discards old queued audio without resetting a live ring.

Callbacks perform no model inference, file/pipe IO, allocation, or locks. Worker
pipe IO and JSON run on background threads. Physical input streams on the output
IOProc are disabled and verified, including for duplex headsets.

See [architecture](../../docs/architecture.md) and [tests](../../docs/testing.md).
