"""Compatibility entry point for the authoritative hardened run manifest.

Requires the successful stage ledger and all fresh outputs; never invents commands or versions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.freeze_results import main  # noqa: E402

if __name__ == "__main__":
    main()
