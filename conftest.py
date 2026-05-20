"""
pytest configuration: ensures the src/ layout is importable without installation.

This file is automatically discovered by pytest and executed before tests run.
It adds the src/ directory to sys.path so that `import ingestion` works in any
environment — installed or not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
