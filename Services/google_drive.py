from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from Config.accounts import AccountStore
from Config.settings import Settings
from Services.errors import CrawlerError


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
FOLDER_MIME = "application/vnd.google-apps.folder"


class GoogleDriveService:
    """Cấu hình OAuth và tải cây thư mục sách lên Google Drive."""

    def __init__(self, settings: Settings) -> None:
        self.store = AccountStore(settings.config_dir)

    def status(self) -> dict[str, Any]:
        config = self.store.get("google_drive")
        return {
            "connected": bool(config.get("token")),
            "enabled": bool(config.get("enabled")),
            "folder_url": str(config.get("folder_url") or ""),
            "folder_id": str(config.get("folder_id") or ""),
        }

    def connect(self, client_secret_path: str) -> None:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as error:
            raise CrawlerError(
                self._dependency_error_message()
            ) from error
        path = Path(client_secret_path).expanduser()
        if not path.is_file():
            raise CrawlerError(f"Không tìm thấy file OAuth: {path}")
        try:
            client_config = json.loads(path.read_text(encoding="utf-8"))
            flow = InstalledAppFlow.from_client_config(client_config, [DRIVE_SCOPE])
            credentials = flow.run_local_server(port=0, open_browser=True)
        except (OSError, ValueError, KeyError) as error:
            raise CrawlerError(f"File OAuth không hợp lệ: {error}") from error
        config = self.store.get("google_drive")
        config.update(
            {
                "client_config": client_config,
                "token": json.loads(credentials.to_json()),
                "enabled": bool(config.get("folder_id")),
            }
        )
        self.store.save("google_drive", config)

    def set_folder(self, folder_url: str) -> str:
        folder_id = self.folder_id_from_url(folder_url)
        config = self.store.get("google_drive")
        if config.get("token"):
            service = self._build_service(config)
            try:
                folder = service.files().get(
                    fileId=folder_id,
                    fields="id,name,mimeType,capabilities(canAddChildren)",
                    supportsAllDrives=True,
                ).execute()
            except Exception as error:
                self._raise_drive_error(error)
            if folder.get("mimeType") != FOLDER_MIME:
                raise CrawlerError("Link đã nhập không phải là thư mục Google Drive.")
            if not folder.get("capabilities", {}).get("canAddChildren", False):
                raise CrawlerError(
                    "Tài khoản OAuth không có quyền thêm file vào thư mục này. "
                    "Hãy cấp quyền Người chỉnh sửa (Editor) cho đúng tài khoản Google."
                )
        config.update(
            {"folder_url": folder_url.strip(), "folder_id": folder_id, "enabled": True}
        )
        self.store.save("google_drive", config)
        return folder_id

    def set_enabled(self, enabled: bool) -> None:
        config = self.store.get("google_drive")
        if enabled and (not config.get("token") or not config.get("folder_id")):
            raise CrawlerError("Hãy đăng nhập OAuth và đặt thư mục Drive trước.")
        config["enabled"] = enabled
        self.store.save("google_drive", config)

    def disconnect(self) -> None:
        config = self.store.get("google_drive")
        config.pop("token", None)
        config.pop("client_config", None)
        config["enabled"] = False
        self.store.save("google_drive", config)

    def upload_download(
        self,
        destination: Path,
        source: str,
        book_title: str = "",
        book_id: int | str | None = None,
    ) -> str | None:
        config = self.store.get("google_drive")
        if not config.get("enabled"):
            return None
        service = self._build_service(config)
        root_id = str(config.get("folder_id") or "")
        if not root_id:
            raise CrawlerError("Chưa cấu hình thư mục Google Drive.")
        try:
            source_id = self._ensure_folder(service, source, root_id)
            local_root = destination if destination.is_dir() else destination.parent
            metadata_title, metadata_id = self._read_book_identity(local_root)
            title = book_title or metadata_title
            identity = book_id if book_id is not None else metadata_id
            remote_name = f"{title} - {identity}" if title and identity else title or local_root.name
            remote_root = self._ensure_folder(
                service,
                remote_name,
                source_id,
                aliases=(local_root.name,),
            )
            if destination.is_file():
                self._upload_file(service, destination, remote_root)
            else:
                self._upload_directory_contents(service, destination, remote_root)
        except Exception as error:
            self._raise_drive_error(error)
        return f"https://drive.google.com/drive/folders/{remote_root}"

    def _build_service(self, config: dict[str, Any]):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as error:
            raise CrawlerError(
                self._dependency_error_message()
            ) from error
        token = config.get("token")
        if not isinstance(token, dict):
            raise CrawlerError("Phiên Google Drive không hợp lệ. Hãy đăng nhập lại.")
        credentials = Credentials.from_authorized_user_info(token, [DRIVE_SCOPE])
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            config["token"] = json.loads(credentials.to_json())
            self.store.save("google_drive", config)
        if not credentials.valid:
            raise CrawlerError("Phiên Google Drive đã hết hạn. Hãy đăng nhập lại.")
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _upload_directory_contents(self, service, directory: Path, parent_id: str) -> None:
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_dir():
                folder_id = self._ensure_folder(service, child.name, parent_id)
                self._upload_directory_contents(service, child, folder_id)
            elif child.is_file():
                self._upload_file(service, child, parent_id)

    @staticmethod
    def _ensure_folder(
        service, name: str, parent_id: str, aliases: tuple[str, ...] = ()
    ) -> str:
        candidates = tuple(dict.fromkeys((name, *aliases)))
        for candidate in candidates:
            escaped = candidate.replace("'", "\\'")
            query = (
                f"name = '{escaped}' and '{parent_id}' in parents and "
                f"mimeType = '{FOLDER_MIME}' and trashed = false"
            )
            result = service.files().list(
                q=query, spaces="drive", fields="files(id,name)", pageSize=1,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            files = result.get("files", [])
            if files:
                folder_id = str(files[0]["id"])
                if candidate != name:
                    service.files().update(
                        fileId=folder_id,
                        body={"name": name},
                        supportsAllDrives=True,
                    ).execute()
                return folder_id
        created = service.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id", supportsAllDrives=True,
        ).execute()
        return str(created["id"])

    @staticmethod
    def _read_book_identity(directory: Path) -> tuple[str, int | str | None]:
        metadata = directory / "book.json"
        if not metadata.is_file():
            return "", None
        try:
            value = json.loads(metadata.read_text(encoding="utf-8"))
            return str(value.get("title") or ""), value.get("id")
        except (OSError, ValueError, json.JSONDecodeError):
            return "", None

    @staticmethod
    def _upload_file(service, path: Path, parent_id: str) -> None:
        from googleapiclient.http import MediaFileUpload

        escaped = path.name.replace("'", "\\'")
        result = service.files().list(
            q=f"name = '{escaped}' and '{parent_id}' in parents and trashed = false",
            spaces="drive", fields="files(id)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        media = MediaFileUpload(str(path), resumable=True)
        files = result.get("files", [])
        if files:
            service.files().update(
                fileId=files[0]["id"], media_body=media, supportsAllDrives=True
            ).execute()
        else:
            service.files().create(
                body={"name": path.name, "parents": [parent_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()

    @staticmethod
    def _raise_drive_error(error: Exception) -> None:
        try:
            from googleapiclient.errors import HttpError
        except ImportError:
            raise CrawlerError(f"Không thể tải lên Google Drive: {error}") from error
        if isinstance(error, CrawlerError):
            raise error
        if isinstance(error, HttpError):
            content = error.content.decode("utf-8", errors="replace")
            if "insufficientParentPermissions" in content:
                raise CrawlerError(
                    "Google Drive từ chối ghi vào thư mục đích. Hãy kiểm tra bạn đã "
                    "đăng nhập đúng tài khoản và tài khoản đó có quyền Người chỉnh sửa (Editor)."
                ) from error
            raise CrawlerError(
                f"Google Drive API báo lỗi HTTP {error.resp.status}: {error.reason}"
            ) from error
        raise CrawlerError(f"Không thể tải lên Google Drive: {error}") from error

    @staticmethod
    def folder_id_from_url(value: str) -> str:
        value = value.strip()
        patterns = (r"/folders/([A-Za-z0-9_-]+)", r"[?&]id=([A-Za-z0-9_-]+)")
        for pattern in patterns:
            match = re.search(pattern, value)
            if match:
                return match.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
            return value
        raise CrawlerError("Link thư mục Google Drive không hợp lệ.")

    @staticmethod
    def _dependency_error_message() -> str:
        project = Path(__file__).resolve().parent.parent
        venv_python = (
            project / ".venv" / "Scripts" / "python.exe"
            if __import__("sys").platform == "win32"
            else project / ".venv" / "bin" / "python"
        )
        if venv_python.exists():
            return (
                "CLI cũ đang dùng Python hệ thống. Hãy thoát hẳn rồi chạy lại: "
                "python main.py"
            )
        return "Thiếu thư viện Google Drive. Hãy chạy: pip install -r requirements.txt"
