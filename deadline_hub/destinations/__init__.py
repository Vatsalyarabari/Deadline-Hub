from .google_tasks import GoogleTasksDestination
from .ics_feed import IcsFeedDestination
from .notion import NotionDestination
from .todoist import TodoistDestination

REGISTRY = {
    "ics_feed": IcsFeedDestination,
    "notion": NotionDestination,
    "google_tasks": GoogleTasksDestination,
    "todoist": TodoistDestination,
}
