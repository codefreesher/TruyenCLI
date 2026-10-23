from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from Config.paths import AppPaths


@dataclass(frozen=True, slots=True)
class Settings:
    output_dir: Path = Path("data")
    timeout: float = 20.0
    user_agent: str = "Mozilla/5.0 (compatible; TruyenCLI/0.1)"
    request_delay: float = 0.5
    config_dir: Path = AppPaths.detect().config_dir
    state_dir: Path = AppPaths.detect().state_dir
    log_dir: Path = AppPaths.detect().log_dir

    @classmethod
    def from_env(cls, output_override: Path | None = None) -> "Settings":
        return cls(
            output_dir=output_override or Path(os.getenv("TRUYEN_OUTPUT_DIR", "data")),
            timeout=float(os.getenv("TRUYEN_TIMEOUT", "20")),
            user_agent=os.getenv(
                "TRUYEN_USER_AGENT", "Mozilla/5.0 (compatible; TruyenCLI/0.1)"
            ),
            request_delay=float(os.getenv("TRUYEN_REQUEST_DELAY", "0.5")),
        )
