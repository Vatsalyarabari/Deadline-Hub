"""Every destination implements this. Add a new app = add one small class."""
from __future__ import annotations


class Destination:
    name = "base"
    # True for outputs that are regenerated whole each run (a calendar file),
    # False for apps where items are created once and updated in place.
    full_rebuild = False

    def begin(self, upcoming):  # called once before create/update
        pass

    def create(self, deadline) -> str:
        """Create the item and return the app's ID for it."""
        raise NotImplementedError

    def update(self, remote_id: str, deadline):
        """Update title/due/course only. Never touch the user's status/checkmark."""
        raise NotImplementedError

    def finish(self, upcoming):  # called once after create/update
        pass
