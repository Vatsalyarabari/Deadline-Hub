from .canvas import CanvasSource
from .ics import IcsSource
from .manual import ManualSource
from .moodle import MoodleSource

REGISTRY = {"ics": IcsSource, "moodle": MoodleSource, "canvas": CanvasSource, "manual": ManualSource}
