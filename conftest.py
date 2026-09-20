import sys
from pathlib import Path

# Make the project root importable as `app.*` regardless of invocation
# directory, matching scripts/export_data.py's own defensive sys.path setup.
sys.path.insert(0, str(Path(__file__).resolve().parent))
