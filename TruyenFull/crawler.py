from __future__ import annotations

import json
import re
import zlib
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from Config.settings import Settings
from Services.base_crawler import BaseCrawler
from Services.catalog import CatalogBook, CatalogStore
from Services.errors import CrawlerError, ParseError
from Services.http_client import HttpClient
from Services.models import Book, Chapter
from Services.output import slugify


class TruyenFullCrawler(BaseCrawler):
    key = "truyenfull"
    name = "Truyện Full"
    base_url = "https://truyenfull.live"
    download_mode = "chapters"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.http = HttpClient(settings)
        self._resolved_urls: dict[int, str] = {}

    def search(self, keyword: str) -> list[Book]:
        url = f"{self.base_url}/tim-kiem/?tukhoa={quote_plus(keyword)}"
        return [self._catalog_to_book(item) for item in self._parse_listing(url)[0]]

    def scan_catalog(
        self,
        limit: int | None = None,
        page_size: int = 25,
        progress: Callable[[int, int], None] | None = None,
        resume: list[CatalogBook] | None = None,
        checkpoint: Callable[[list[CatalogBook], int], None] | None = None,
    ) -> tuple[list[CatalogBook], int]:
        if limit is not None and limit < 1:
            raise CrawlerError("--limit phải lớn hơn hoặc bằng 1.")
        # Duyệt lại các trang danh sách (rẻ) nhưng bỏ qua bước đọc chi tiết
        # (đắt) với những truyện đã quét dở.
        books: dict[str, CatalogBook] = {book.source_url: book for book in resume or []}
        page, total_pages = 1, 1
        detected_page_size = page_size
        while page <= total_pages and (limit is None or len(books) < limit):
            url = f"{self.base_url}/danh-sach/truyen-moi/"
            if page > 1:
                url += f"trang-{page}/"
            batch, detected_pages = self._parse_listing(url)
            total_pages = max(total_pages, detected_pages)
            if page == 1 and batch:
                detected_page_size = len(batch)
            for book in batch:
                # Trang danh sách của Truyện Full không luôn hiển thị thể loại.
                # Đọc trang chi tiết để catalog có đủ dữ liệu lọc theo thể loại.
                if book.source_url not in books:
                    books[book.source_url] = self._enrich_catalog_book(book)
                if limit is not None and len(books) >= limit:
                    break
                if progress:
                    estimated = total_pages * detected_page_size
                    progress(len(books), min(estimated, limit or estimated))
            estimated = total_pages * detected_page_size
            if checkpoint:
                checkpoint(list(books.values()), estimated)
            if progress:
                progress(len(books), min(estimated, limit or estimated))
            else:
                print(f"Đang quét: {len(books)}/{min(estimated, limit or estimated)}", end="\r")
            if not batch:
                break
            page += 1
        if not progress:
            print(" " * 60, end="\r")
        return list(books.values()), total_pages * detected_page_size

    def get_catalog_book(self, value: int | str) -> CatalogBook:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            url = value
        else:
            numeric_id = int(value)
            url = self._resolved_urls.get(numeric_id, "")
            if not url:
                books = CatalogStore(self.settings.output_dir, self.key).load()
                match = next((book for book in books if book.id == numeric_id), None)
                if match is None:
                    raise CrawlerError(f"Không tìm thấy truyện ID {value} trong catalog.")
                url = match.source_url
        book, categories = self._read_book(url, include_chapters=False)
        result = CatalogBook(
            id=self._stable_id(book.url), title=book.title,
            authors=[book.author] if book.author else [], formats=["TXT"],
            categories=categories, cover_url=book.cover_url, source_url=book.url,
        )
        self._resolved_urls[result.id] = result.source_url
        return result

    def book_id_from_input(self, value: str) -> int | str | None:
        value = value.strip()
        if value.isdigit():
            return int(value)
        if value.startswith(("http://", "https://")):
            parsed = urlparse(value)
            if parsed.hostname in {"truyenfull.live", "www.truyenfull.live"}:
                return value.split("#", 1)[0].split("?", 1)[0]
        return None

    def get_book(self, url: str) -> Book:
        return self._read_book(url, include_chapters=True)[0]

    def _read_book(self, url: str, include_chapters: bool) -> tuple[Book, list[str]]:
        soup = self._soup(url)
        title_node = soup.select_one("h1.title") or soup.select_one("h1")
        if not title_node:
            raise ParseError(f"Không đọc được tên truyện từ {url}")
        canonical = soup.select_one('link[rel="canonical"]')
        canonical_url = str(canonical.get("href") if canonical else url)
        author_node = soup.select_one('.info a[href*="/tac-gia/"]')
        categories = [x.get_text(" ", strip=True) for x in soup.select('.info a[href*="/the-loai/"]')]
        cover = soup.select_one(".book img") or soup.select_one(".books img")
        desc = soup.select_one(".desc-text")
        book = Book(
            title=title_node.get_text(" ", strip=True), url=canonical_url,
            author=author_node.get_text(" ", strip=True) if author_node else "",
            description=desc.get_text("\n", strip=True) if desc else "",
            cover_url=str(cover.get("src") or cover.get("data-cfsrc") or "") if cover else "",
        )
        if include_chapters:
            book.chapters = self._get_all_chapter_links(soup, canonical_url)
        return book, categories

    def get_chapter(self, url: str) -> Chapter:
        soup = self._soup(url)
        title, content = soup.select_one(".chapter-title"), soup.select_one("#chapter-c")
        if not content:
            raise ParseError(f"Không đọc được nội dung chương từ {url}")
        for unwanted in content.select("script, style, .ads, [id*='ads']"):
            unwanted.decompose()
        match = re.search(r"chuong-(\d+)", url)
        return Chapter(
            title=title.get_text(" ", strip=True) if title else "Chương", url=url,
            content=content.get_text("\n", strip=True),
            number=int(match.group(1)) if match else None,
        )

    def download(
        self, book_id: int | str, file_format: str, directory: Path,
        start: int = 1, limit: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        if start < 1:
            raise CrawlerError("Chương bắt đầu phải lớn hơn hoặc bằng 1.")
        if limit is not None and limit < 1:
            raise CrawlerError("Số chương cần tải phải lớn hơn hoặc bằng 1.")
        detail = self.get_catalog_book(book_id)
        book, categories = self._read_book(detail.source_url, include_chapters=True)
        selected = book.chapters[start - 1:]
        if limit is not None:
            selected = selected[:limit]
        if not selected:
            raise CrawlerError("Không có chương nào trong khoảng đã chọn.")
        book_slug = slugify(book.title)
        book_dir = directory / f"{book_slug}-{detail.id}"
        chapter_dir = book_dir / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": detail.id, "title": book.title, "author": book.author,
            "categories": categories, "source_url": book.url,
            "total_chapters": len(book.chapters),
        }
        (book_dir / "book.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        for index, link in enumerate(selected, 1):
            chapter = self.get_chapter(link.url)
            number = chapter.number or start + index - 1
            (chapter_dir / f"chapter-{number:05d}.txt").write_text(
                f"{chapter.title}\n{chapter.url}\n\n{chapter.content}\n", encoding="utf-8"
            )
            if progress:
                progress(index, len(selected))
        return book_dir.resolve()

    def _get_all_chapter_links(self, first: BeautifulSoup, book_url: str) -> list[Chapter]:
        chapters: dict[str, Chapter] = {}
        for page in range(1, self._max_page(first) + 1):
            soup = first if page == 1 else self._soup(urljoin(book_url, f"trang-{page}/"))
            for node in soup.select(".list-chapter a[href]"):
                url = urljoin(self.base_url, str(node.get("href")))
                chapters[url] = Chapter(title=node.get_text(" ", strip=True), url=url)
        return list(chapters.values())

    def _parse_listing(self, url: str) -> tuple[list[CatalogBook], int]:
        soup = self._soup(url)
        books = []
        for row in soup.select(".list-truyen .row"):
            link = row.select_one("h3.truyen-title a[href]")
            if not link:
                continue
            source_url = urljoin(self.base_url, str(link.get("href")))
            author = row.select_one(".author")
            categories = [
                node.get_text(" ", strip=True)
                for node in row.select('a[href*="/the-loai/"]')
                if node.get_text(" ", strip=True)
            ]
            books.append(CatalogBook(
                id=self._stable_id(source_url), title=link.get_text(" ", strip=True),
                authors=[author.get_text(" ", strip=True)] if author else [],
                formats=["TXT"], categories=list(dict.fromkeys(categories)),
                source_url=source_url,
            ))
        return books, self._max_page(soup)

    def _enrich_catalog_book(self, item: CatalogBook) -> CatalogBook:
        """Bổ sung tác giả, ảnh bìa và thể loại từ trang chi tiết truyện."""
        try:
            book, categories = self._read_book(item.source_url, include_chapters=False)
        except CrawlerError:
            # Một trang lỗi tạm thời không làm mất toàn bộ kết quả quét dài.
            return item
        return CatalogBook(
            id=item.id,
            title=book.title or item.title,
            authors=[book.author] if book.author else item.authors,
            formats=["TXT"],
            categories=list(dict.fromkeys(categories or item.categories)),
            cover_url=book.cover_url or item.cover_url,
            source_url=book.url or item.source_url,
        )

    def _soup(self, url: str) -> BeautifulSoup:
        html = self.http.get_text(url)
        if "Just a moment" in html or "challenge-platform" in html:
            raise CrawlerError("Truyện Full đang yêu cầu xác minh Cloudflare. Hãy thử lại sau.")
        return BeautifulSoup(html, "html.parser")

    @staticmethod
    def _max_page(soup: BeautifulSoup) -> int:
        pages = []
        for node in soup.select(".pagination a[href]"):
            match = re.search(r"/trang-(\d+)/", str(node.get("href")))
            if match:
                pages.append(int(match.group(1)))
        return max(pages, default=1)

    @staticmethod
    def _stable_id(url: str) -> int:
        return zlib.crc32(url.rstrip("/").encode("utf-8")) & 0xFFFFFFFF

    @staticmethod
    def _catalog_to_book(item: CatalogBook) -> Book:
        return Book(title=item.title, url=item.source_url, author=", ".join(item.authors))
