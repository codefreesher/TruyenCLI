from __future__ import annotations

import json
import re
import zlib
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from Config.settings import Settings
from Services.base_crawler import BaseCrawler
from Services.catalog import CatalogBook
from Services.errors import CrawlerError, ParseError
from Services.http_client import HttpClient
from Services.models import Book, Chapter
from Services.output import slugify


class MeoCamMapCrawler(BaseCrawler):
    key = "meocammap"
    name = "Mèo Cam Mập"
    base_url = "https://meocammap.com"
    download_mode = "chapters"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.http = HttpClient(settings)
        self._resolved_urls: dict[int, str] = {}

    def search(self, keyword: str) -> list[Book]:
        url = f"{self.base_url}/api/search?{urlencode({'q': keyword, 'limit': 20})}"
        payload = self.http.get_json(url)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [
            Book(
                title=str(item.get("title") or "Không tên"),
                url=f"{self.base_url}/stories/{item['id']}",
                cover_url=str(item.get("cover_url") or ""),
            )
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]

    def get_book(self, url: str) -> Book:
        html = self.http.get_text(self._story_url(url))
        soup = BeautifulSoup(html, "html.parser")
        metadata = self._book_metadata(soup)
        canonical = soup.select_one('link[rel="canonical"]')
        book_url = str(canonical.get("href") if canonical else self._story_url(url))
        story_id = self._story_id(book_url)
        chapters: dict[str, Chapter] = {}
        # Next.js nhúng danh sách đầy đủ vào initialChapters, trong khi phần HTML
        # hiển thị có thể chỉ render một phần danh sách vì streaming/lazy loading.
        for match in re.finditer(
            r'\\"id\\":\\"([A-Za-z0-9_-]+)\\",\\"no\\":(\d+)', html
        ):
            chapter_id, raw_number = match.groups()
            number = int(raw_number)
            chapter_url = f"{self.base_url}/read/{story_id}/{chapter_id}"
            chapters[chapter_url] = Chapter(
                title=f"Chương {number}", url=chapter_url, number=number
            )
        for node in soup.select(f'a[href^="/read/{story_id}/"]'):
            href = str(node.get("href") or "")
            heading = node.select_one("h3[title]")
            if not heading:
                continue
            match = re.search(r"(\d+)", str(heading.get("title") or heading.get_text()))
            number = int(match.group(1)) if match else None
            chapter_url = urljoin(self.base_url, href)
            chapters[chapter_url] = Chapter(
                title=str(heading.get("title") or f"Chương {number or ''}").strip(),
                url=chapter_url,
                number=number,
            )
        ordered = sorted(chapters.values(), key=lambda item: item.number or 0)
        if not ordered:
            raise ParseError(f"Không đọc được danh sách chương từ {book_url}")
        author = metadata.get("author", {})
        return Book(
            title=str(metadata.get("name") or self._title(soup)),
            url=book_url,
            author=str(author.get("name") or "") if isinstance(author, dict) else str(author or ""),
            description=str(metadata.get("description") or ""),
            cover_url=str(metadata.get("image") or ""),
            chapters=ordered,
        )

    def get_chapter(self, url: str) -> Chapter:
        chapter_url = urljoin(self.base_url, url)
        html = self.http.get_text(chapter_url)
        story_id, chapter_id = self._chapter_ids(chapter_url)
        token_match = re.search(r'\\"token\\":\\"([^\\"]+)\\"', html)
        if not token_match:
            raise ParseError(f"Không lấy được token nội dung chương từ {chapter_url}")
        query = urlencode(
            {"storyId": story_id, "chapterId": chapter_id, "token": token_match.group(1), "v": 2}
        )
        payload = self.http.get_json(
            f"{self.base_url}/read/content?{query}", headers={"Referer": chapter_url}
        )
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise CrawlerError(f"Không tải được nội dung chương từ {chapter_url}")
        content_html = str(payload.get("content") or "")
        if payload.get("scrambled"):
            content_html = self._unscramble(content_html)
        content = BeautifulSoup(content_html, "html.parser").get_text("\n", strip=True)
        title_node = BeautifulSoup(html, "html.parser").select_one('meta[property="og:title"]')
        raw_title = str(title_node.get("content") or "") if title_node else ""
        number_match = re.search(r"Chương\s+(\d+)", raw_title, re.IGNORECASE)
        number = int(number_match.group(1)) if number_match else None
        return Chapter(
            title=f"Chương {number}" if number else raw_title or "Chương",
            url=chapter_url,
            content=content,
            number=number,
        )

    def book_id_from_input(self, value: str) -> int | str | None:
        value = value.strip()
        if value.startswith(("http://", "https://")):
            parsed = urlparse(value)
            if parsed.hostname in {"meocammap.com", "www.meocammap.com"} and "/stories/" in parsed.path:
                return self._story_url(value)
        if value.isdigit():
            return int(value)
        return None

    def get_catalog_book(self, value: int | str) -> CatalogBook:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            url = value
        else:
            url = self._resolved_urls.get(int(value), "")
            if not url:
                raise CrawlerError("Hãy nhập URL truyện Mèo Cam Mập đầy đủ.")
        book = self.get_book(url)
        book_id = self._stable_id(book.url)
        self._resolved_urls[book_id] = book.url
        return CatalogBook(
            id=book_id,
            title=book.title,
            authors=[book.author] if book.author else [],
            formats=["TXT"],
            cover_url=book.cover_url,
            source_url=book.url,
        )

    def download(
        self,
        book_id: int | str,
        file_format: str,
        directory: Path,
        start: int = 1,
        limit: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        if file_format.upper() != "TXT":
            raise CrawlerError("Mèo Cam Mập chỉ hỗ trợ định dạng TXT.")
        if start < 1 or (limit is not None and limit < 1):
            raise CrawlerError("Chương bắt đầu và số chương phải lớn hơn hoặc bằng 1.")
        detail = self.get_catalog_book(book_id)
        book = self.get_book(detail.source_url)
        selected = book.chapters[start - 1 :]
        if limit is not None:
            selected = selected[:limit]
        if not selected:
            raise CrawlerError("Không có chương nào trong khoảng đã chọn.")
        book_dir = directory / f"{slugify(book.title)}-{detail.id}"
        chapter_dir = book_dir / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": detail.id,
            "title": book.title,
            "author": book.author,
            "source_url": book.url,
            "total_chapters": len(book.chapters),
        }
        (book_dir / "book.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for index, link in enumerate(selected, 1):
            chapter = self.get_chapter(link.url)
            number = chapter.number or start + index - 1
            (chapter_dir / f"chapter-{number:05d}.txt").write_text(
                f"{chapter.title}\n{chapter.url}\n\n{chapter.content}\n", encoding="utf-8"
            )
            if progress:
                progress(index, len(selected))
        return book_dir.resolve()

    @staticmethod
    def _book_metadata(soup: BeautifulSoup) -> dict:
        for node in soup.select('script[type="application/ld+json"]'):
            try:
                value = json.loads(node.string or node.get_text())
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("@type") == "Book":
                return value
        return {}

    @staticmethod
    def _title(soup: BeautifulSoup) -> str:
        node = soup.select_one("h1")
        if not node:
            raise ParseError("Không đọc được tên truyện Mèo Cam Mập.")
        return node.get_text(" ", strip=True)

    def _story_url(self, value: str) -> str:
        story_id = self._story_id(value)
        return f"{self.base_url}/stories/{story_id}"

    @staticmethod
    def _story_id(url: str) -> str:
        match = re.search(r"/stories/([A-Za-z0-9_-]+)", url)
        if not match:
            raise CrawlerError("URL truyện Mèo Cam Mập không hợp lệ.")
        return match.group(1)

    @staticmethod
    def _chapter_ids(url: str) -> tuple[str, str]:
        match = re.search(r"/read/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)", url)
        if not match:
            raise CrawlerError("URL chương Mèo Cam Mập không hợp lệ.")
        return match.group(1), match.group(2)

    @staticmethod
    def _stable_id(url: str) -> int:
        return zlib.crc32(url.rstrip("/").encode("utf-8")) & 0xFFFFFFFF

    @staticmethod
    def _unscramble(content: str) -> str:
        parts = re.split(r"(\s+)", content)
        word_indexes = [i for i, value in enumerate(parts) if value and not value.isspace()]
        order = sorted(
            range(len(word_indexes)),
            key=lambda index: ((index + 1) * 0x9E3779B1) & 0xFFFFFFFF,
        )
        restored = parts.copy()
        for source, target in enumerate(order):
            restored[word_indexes[target]] = parts[word_indexes[source]]
        return "".join(restored)
