from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from Services.models import Book


def slugify(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-") or "truyen"


class OutputWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def write(self, book: Book, source: str) -> Path:
        directory = self.output_dir / source
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{slugify(book.title)}.json"
        destination.write_text(
            json.dumps(book.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return destination.resolve()
