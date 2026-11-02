"""Destination: Google Tasks (shows up in the Google Tasks phone app, Gmail and Calendar).

First run opens a browser to sign in with Google and saves a token locally.
Needs a free OAuth client from Google Cloud (see README).
"""
from __future__ import annotations

from pathlib import Path

from .base import Destination

SCOPES = ["https://www.googleapis.com/auth/tasks"]


class GoogleTasksDestination(Destination):
    def __init__(self, name: str, client_secret_file: str, token_file: str = "google_token.json",
                 list_name: str = "School Deadlines"):
        self.name = name
        self.client_secret_file = Path(client_secret_file).expanduser()
        self.token_file = Path(token_file).expanduser()
        self.list_name = list_name
        self.svc = None
        self.list_id = None

    def _service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secret_file), SCOPES)
                creds = flow.run_local_server(port=0)
            self.token_file.write_text(creds.to_json())
        return build("tasks", "v1", credentials=creds, cache_discovery=False)

    def begin(self, upcoming):
        self.svc = self._service()
        lists = self.svc.tasklists().list(maxResults=100).execute().get("items", [])
        match = next((l for l in lists if l["title"] == self.list_name), None)
        if match is None:
            match = self.svc.tasklists().insert(body={"title": self.list_name}).execute()
        self.list_id = match["id"]

    def _body(self, d) -> dict:
        # Google Tasks stores a date only, so the exact time goes in the notes.
        notes = [f"Due {d.due.strftime('%a %b %d, %I:%M %p')}"]
        if d.course:
            notes.append(d.course)
        if d.url:
            notes.append(d.url)
        return {
            "title": d.display_title,
            "notes": "\n".join(notes),
            "due": d.due.strftime("%Y-%m-%dT00:00:00.000Z"),
        }

    def create(self, d) -> str:
        task = self.svc.tasks().insert(tasklist=self.list_id, body=self._body(d)).execute()
        return task["id"]

    def update(self, remote_id, d):
        # patch leaves "status" (the checkmark) alone
        self.svc.tasks().patch(tasklist=self.list_id, task=remote_id, body=self._body(d)).execute()
