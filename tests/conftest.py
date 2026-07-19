"""Test configuration for local source imports."""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "python"
sys.path.insert(0, str(SOURCE_ROOT))
