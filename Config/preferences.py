from __future__ import annotations

import shutil
from pathlib import Path

from Config.accounts import AccountStore
from Config.settings import Settings


class DownloadPreferences:
    def __init__(self, settings: Settings) -> None:
        self.store = AccountStore(settings.config_dir)

    @property
    def keep_local(self) -> bool:
        return bool(self.store.get("preferences").get("keep_local", True))

    def set_keep_local(self, enabled: bool) -> None:
        value = self.store.get("preferences")
        value["keep_local"] = enabled
        self.store.save("preferences", value)


def remove_local_download(destination: Path) -> Path:
    """Xóa thư mục của một truyện sau khi bản Drive đã được xác nhận."""
    if destination.is_dir():
        root = destination
        shutil.rmtree(root)
    elif destination.parent.parent.parent.name == "downloads":
        root = destination.parent
        if root.exists():
            shutil.rmtree(root)
    else:
        root = destination
        destination.unlink(missing_ok=True)
    return root
