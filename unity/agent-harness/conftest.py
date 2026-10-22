"""Make ``cli_anything`` importable when running pytest from the repo root
or without installing the package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
