from __future__ import annotations

import inspect
import json
import os
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from Services.errors import CrawlerError


@dataclass(slots=True)
class CatalogBook:
    id: int
    title: str
    authors: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    cover_url: str = ""
    source_url: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CatalogBook":
        return cls(
            id=int(value["id"]),
            title=str(value.get("title") or "Không tên"),
            authors=[str(item) for item in value.get("authors", [])],
            formats=[str(item).upper() for item in value.get("formats", [])],
            categories=[str(item) for item in value.get("categories", [])],
            cover_url=str(value.get("cover_url") or ""),
            source_url=str(value.get("source_url") or ""),
        )


class CatalogStore:
    def __init__(self, output_dir: Path, source: str) -> None:
        self.path = output_dir / "catalogs" / f"{source}.json"

    @property
    def partial_path(self) -> Path:
        return self.path.with_suffix(".partial.json")

    def save_partial(self, books: list[CatalogBook], total: int) -> None:
        self._write(self.partial_path, books, total)

    def load_partial(self) -> list[CatalogBook]:
        try:
            payload = json.loads(self.partial_path.read_text(encoding="utf-8"))
            return [CatalogBook.from_dict(item) for item in payload["books"]]
        except (OSError, KeyError, TypeError, ValueError):
            return []

    def clear_partial(self) -> None:
        self.partial_path.unlink(missing_ok=True)

    def save(self, books: list[CatalogBook], total: int) -> Path:
        self._write(self.path, books, total)
        self.clear_partial()
        return self.path.resolve()

    def _write(self, path: Path, books: list[CatalogBook], total: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": self.path.stem.removesuffix(".partial"),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "total_available": total,
            "saved_count": len(books),
            "books": [asdict(book) for book in books],
        }
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)

    def load(self) -> list[CatalogBook]:
        if not self.path.exists():
            raise CrawlerError(f"Chưa có catalog {self.path}. Hãy chạy lệnh scan trước.")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return [CatalogBook.from_dict(item) for item in payload["books"]]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise CrawlerError(f"Catalog bị lỗi hoặc sai cấu trúc: {self.path}") from error

    def search(
        self,
        query: str,
        limit: int | None = 20,
        category: str | None = None,
    ) -> list[CatalogBook]:
        normalized = normalize_search(query)
        normalized_category = normalize_search(category or "")
        matches = [
            book for book in self.load()
            if (
                not normalized_category
                or any(normalize_search(item) == normalized_category for item in book.categories)
            )
            and (
                not normalized
                or normalized in normalize_search(book.title)
                or any(normalized in normalize_search(author) for author in book.authors)
            )
        ]
        if normalized:
            matches.sort(key=lambda book: search_rank(book, normalized))
        return matches if limit is None else matches[:limit]

    def category_counts(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for book in self.load():
            for category in book.categories:
                counts[category] = counts.get(category, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], normalize_search(item[0])))


def normalize_search(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d").casefold().strip()
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def search_rank(book: CatalogBook, query: str) -> tuple[int, str]:
    title = normalize_search(book.title)
    authors = [normalize_search(author) for author in book.authors]
    if title == query:
        rank = 0
    elif title.startswith(query):
        rank = 1
    elif query in title:
        rank = 2
    elif query in authors:
        rank = 3
    else:
        rank = 4
    return rank, title


def scan_and_save(
    crawler,
    store: CatalogStore,
    limit: int | None,
    progress: Callable[[int, int], None] | None = None,
    save_interval: float = 30.0,
) -> tuple[int, int, Path]:
    """Quét catalog; nếu lỗi giữa chừng thì giữ bản dở để lần sau quét tiếp."""
    kwargs: dict[str, Any] = {}
    if progress:
        kwargs["progress"] = progress
    latest: list[tuple[list[CatalogBook], int]] = []
    if "resume" in inspect.signature(crawler.scan_catalog).parameters:
        resume = store.load_partial() if limit is None else []
        if resume:
            message = f"Tiếp tục quét từ {len(resume)} sách đã lưu dở."
            if progress:
                progress(len(resume), len(resume))
            else:
                print(message)
        last_saved = [time.monotonic()]

        def checkpoint(books: list[CatalogBook], total: int) -> None:
            latest[:] = [(books, total)]
            # Ghi theo thời gian để không tốn I/O file lớn sau mỗi trang.
            if time.monotonic() - last_saved[0] >= save_interval:
                store.save_partial(books, total)
                last_saved[0] = time.monotonic()

        kwargs.update(resume=resume, checkpoint=checkpoint)
    try:
        books, total = crawler.scan_catalog(limit=limit, **kwargs)
    except BaseException:
        if latest and limit is None:
            store.save_partial(*latest[0])
        raise
    return len(books), total, store.save(books, total)
