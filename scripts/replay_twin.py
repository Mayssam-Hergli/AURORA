"""Alias of twin/service.py (the live twin), kept so older commands and tests keep working.

python scripts/replay_twin.py --sats A,C-L --speed 20
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from twin.service import (  # noqa: E402,F401
    CARRY,
    ReplayTwin,
    TwinService,
    build_twin,
    main,
    parse_fault,
    parse_sats,
    to_contract,
)

if __name__ == "__main__":
    sys.exit(main())
