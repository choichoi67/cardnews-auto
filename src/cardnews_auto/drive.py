from pathlib import Path
from datetime import datetime
import re

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _service(cfg: dict):
    token_file = Path(cfg.get("token_file", "token.json"))
    creds = Credentials.from_authorized_user_file(token_file, SCOPES) if token_file.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(cfg["credentials_file"], SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=creds)


def test_connection(cfg: dict) -> dict:
    """Authorize Drive and verify write access to the configured destination."""
    service = _service(cfg)
    user = service.about().get(fields="user(displayName,emailAddress)").execute().get("user", {})
    parent = cfg.get("folder_id") or "root"
    probe = service.files().create(
        body={
            "name": ".cardnews-connection-test",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent],
        },
        fields="id",
    ).execute()
    service.files().update(fileId=probe["id"], body={"trashed": True}).execute()
    return {"email": user.get("emailAddress", ""), "name": user.get("displayName", ""), "folder_id": parent}


def _folder(service, name: str, parent: str | None) -> str:
    escaped = name.replace("'", "\\'")
    clauses = [
        f"name = '{escaped}'",
        "mimeType = 'application/vnd.google-apps.folder'",
        "trashed = false",
    ]
    if parent:
        clauses.append(f"'{parent}' in parents")
    found = service.files().list(q=" and ".join(clauses), fields="files(id)", pageSize=1).execute().get("files", [])
    if found:
        return found[0]["id"]
    result = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", **({"parents": [parent]} if parent else {})},
        fields="id",
    ).execute()
    return result["id"]


def upload_files(paths: list[Path], cfg: dict, folder_name: str, magazine_name: str = "Magazine") -> dict:
    service = _service(cfg)
    now = datetime.now().astimezone()
    parent = cfg.get("folder_id") or _folder(service, cfg.get("root_folder_name", "Cardnews Auto"), None)
    # 루트가 지저분해지지 않으면서도 날짜별 탐색이 쉬운 고정 구조다.
    hierarchy = [now.strftime("%Y"), now.strftime("%m"), now.strftime("%Y-%m-%d"), magazine_name]
    for name in hierarchy:
        parent = _folder(service, name, parent)
    topic = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", folder_name).strip() or "cardnews"
    job_name = f"{now:%H%M}_{topic}"
    folder_id = _folder(service, job_name, parent)
    ids = [
        service.files().create(
            body={"name": path.name, "parents": [folder_id]},
            media_body=MediaFileUpload(str(path), resumable=True), fields="id",
        ).execute()["id"]
        for path in paths
    ]
    return {
        "file_ids": ids,
        "folder_id": folder_id,
        "folder_url": f"https://drive.google.com/drive/folders/{folder_id}",
        "path": "/".join([*hierarchy, job_name]),
    }
