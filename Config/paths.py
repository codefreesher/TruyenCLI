from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    config_dir: Path
    state_dir: Path
    log_dir: Path

    @classmethod
    def detect(cls) -> "AppPaths":
        if sys.platform == "win32":
            root = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TruyenCLI"
            return cls(root / "config", root / "state", root / "logs")
        if sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "TruyenCLI"
            return cls(root / "config", root / "state", Path.home() / "Library" / "Logs" / "TruyenCLI")
        config = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "truyen-cli"
        state = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "truyen-cli"
        return cls(config, state, state / "logs")
