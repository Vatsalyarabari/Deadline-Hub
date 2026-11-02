"""Double-click to open Deadline Hub (runs without a console window)."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from deadline_hub.app import main  # noqa: E402

main(sys.argv[1:], project_dir=root)
