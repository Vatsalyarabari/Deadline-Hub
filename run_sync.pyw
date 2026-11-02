"""Used by background sync (scheduled task) when running from the source folder."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deadline_hub.app import main  # noqa: E402

main(["--sync"])
