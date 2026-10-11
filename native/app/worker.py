"""Bundled entry point; runtime code lives in the checkout's voicerelay package."""

import sys
from pathlib import Path

if __name__ == "__main__":
    try:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
        sys.path.insert(0, str(root))
        from voicerelay.worker import main

        main()
    except Exception as error:
        import json

        print(json.dumps({"type": "error", "message": str(error)}), flush=True)
        raise
