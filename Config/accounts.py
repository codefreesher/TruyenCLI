from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from Config.paths import AppPaths
from Services.errors import CrawlerError


class AccountStore:
    """Lưu token đã mã hóa, gắn với người dùng và máy hiện tại."""

    def __init__(
        self,
        config_dir: Path | None = None,
        legacy_path: Path | None = None,
    ) -> None:
        self.config_dir = config_dir or AppPaths.detect().config_dir
        self.path = self.config_dir / "accounts.enc"
        self.legacy_path = legacy_path
        self._cipher = Fernet(self._derive_key())
        self._migrate_plaintext()

    def get(self, source: str) -> dict[str, Any]:
        return dict(self._read().get(source, {}))

    def save(self, source: str, account: dict[str, Any]) -> None:
        data = self._read()
        data[source] = account
        self._write(data)

    def remove(self, source: str) -> None:
        data = self._read()
        data.pop(source, None)
        self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            plaintext = self._cipher.decrypt(self.path.read_bytes())
            value = json.loads(plaintext.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CrawlerError(
                f"Không thể giải mã phiên đăng nhập tại {self.path}. "
                "File có thể thuộc máy hoặc người dùng khác."
            ) from error

    def _write(self, data: dict[str, Any]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
        temporary = self.path.with_suffix(".enc.tmp")
        temporary.write_bytes(self._cipher.encrypt(plaintext))
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def _migrate_plaintext(self) -> None:
        if self.path.exists() or not self.legacy_path or not self.legacy_path.exists():
            return
        try:
            value = json.loads(self.legacy_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("invalid account data")
            self._write(value)
            self.legacy_path.unlink()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise CrawlerError(
                f"Không thể chuyển file tài khoản cũ {self.legacy_path}."
            ) from error

    @staticmethod
    def _derive_key() -> bytes:
        identity_parts = [platform.node(), str(Path.home())]
        if hasattr(os, "getuid"):
            identity_parts.append(str(os.getuid()))
        machine_id = Path("/etc/machine-id")
        if machine_id.exists():
            try:
                identity_parts.append(machine_id.read_text(encoding="utf-8").strip())
            except OSError:
                pass
        material = "\0".join(identity_parts).encode("utf-8")
        key = hashlib.pbkdf2_hmac(
            "sha256",
            material,
            b"truyen-cli-account-store-v1",
            600_000,
            dklen=32,
        )
        return base64.urlsafe_b64encode(key)
