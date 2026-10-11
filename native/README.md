# Native application

The AppKit UI and Core Audio route live in `app/`. From the repository root:

```sh
bash scripts/setup.sh
bash native/build_app.sh
open native/build/VoiceRelay.app
```

Builds require Apple Silicon and macOS 14.2+. The resulting app references this
checkout and its `.venv` and `.runtime` directories; rebuild after moving them.
See the [main README](../README.md) for use and troubleshooting.
