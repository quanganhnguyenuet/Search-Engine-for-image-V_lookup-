"""Allow the scripts to run from a repo checkout without installing local packages."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("embedding", "retrieval", "preprocessing", "evaluation"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
