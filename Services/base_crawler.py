from __future__ import annotations

from abc import ABC, abstractmethod

from Config.settings import Settings
from Services.models import Book, Chapter


class BaseCrawler(ABC):
    key: str
    name: str
    base_url: str

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    def search(self, keyword: str) -> list[Book]:
        """Tìm và trả về các truyện chưa cần tải danh sách chương."""

    @abstractmethod
    def get_book(self, url: str) -> Book:
        """Đọc metadata và danh sách chương của một truyện."""

    @abstractmethod
    def get_chapter(self, url: str) -> Chapter:
        """Đọc nội dung của một chương."""

    def crawl(self, url: str, start: int = 1, limit: int | None = None) -> Book:
        book = self.get_book(url)
        selected = book.chapters[start - 1 :]
        if limit is not None:
            if limit < 1:
                raise ValueError("--limit phải lớn hơn hoặc bằng 1.")
            selected = selected[:limit]
        book.chapters = [self.get_chapter(chapter.url) for chapter in selected]
        return book
