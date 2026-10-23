from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlencode, urlparse

from Config.settings import Settings
from Config.accounts import AccountStore
from Services.base_crawler import BaseCrawler
from Services.catalog import CatalogBook
from Services.errors import CrawlerError
from Services.http_client import HttpClient
from Services.models import Book, Chapter
from Services.output import slugify


class KhoSachCrawler(BaseCrawler):
    key = "khosach"
    name = "Kho Sách"
    base_url = "https://khosach.mnd.vn"
    api_url = f"{base_url}/api"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.accounts = AccountStore(
            settings.config_dir,
            legacy_path=settings.output_dir / "config" / "accounts.json",
        )
        account = self.accounts.get(self.key)
        self.http = HttpClient(settings, str(account.get("access_token") or ""))

    def account_status(self) -> dict[str, str | bool]:
        account = self.accounts.get(self.key)
        return {
            "logged_in": bool(account.get("access_token")),
            "username": str(account.get("username") or ""),
        }

    def login(self, username: str, password: str) -> dict:
        if not username or not password:
            raise CrawlerError("Tên đăng nhập và mật khẩu không được để trống.")
        data = self.http.post_form_json(
            f"{self.api_url}/auth/login",
            {"username": username, "password": password},
        )
        access_token = str(data.get("access_token") or "")
        if not access_token:
            raise CrawlerError("Kho Sách không trả về token đăng nhập.")
        account = {
            "username": username,
            "access_token": access_token,
            "refresh_token": str(data.get("refresh_token") or ""),
        }
        self.accounts.save(self.key, account)
        self.http.access_token = access_token
        return account

    def logout(self) -> None:
        self.accounts.remove(self.key)
        self.http.access_token = ""

    def search(self, keyword: str) -> list[Book]:
        params = urlencode({"q": keyword, "limit": 50})
        data = self.http.get_json(f"{self.api_url}/books/search/?{params}")
        return [self._to_book(item) for item in data.get("books", [])]

    def get_book(self, url: str) -> Book:
        book_id = self.book_id_from_input(url)
        if book_id is None:
            raise CrawlerError("Link hoặc ID sách Kho Sách không hợp lệ.")
        return self._to_book(self.get_catalog_book(book_id))

    def get_chapter(self, url: str) -> Chapter:
        raise CrawlerError("Kho Sách cung cấp ebook, không cung cấp chương rời.")

    def crawl(self, url: str, start: int = 1, limit: int | None = None) -> Book:
        raise CrawlerError("Hãy dùng lệnh download để tải ebook từ Kho Sách.")

    def scan_catalog(
        self,
        limit: int | None = None,
        page_size: int = 100,
        progress: Callable[[int, int], None] | None = None,
        resume: list[CatalogBook] | None = None,
        checkpoint: Callable[[list[CatalogBook], int], None] | None = None,
    ) -> tuple[list[CatalogBook], int]:
        if limit is not None and limit < 1:
            raise CrawlerError("--limit phải lớn hơn hoặc bằng 1.")
        # Chỉ giữ các trang đầy đủ đã quét để tiếp tục đúng vị trí trang.
        books: list[CatalogBook] = list(resume or [])[: len(resume or []) // page_size * page_size]
        page = len(books) // page_size + 1
        total = 0
        while limit is None or len(books) < limit:
            size = min(page_size, limit - len(books)) if limit else page_size
            params = urlencode({"page": page, "per_page": size})
            data = self.http.get_json(f"{self.api_url}/books/?{params}")
            total = int(data.get("total", 0))
            batch = [self._to_catalog_book(item) for item in data.get("books", [])]
            books.extend(batch)
            if checkpoint:
                checkpoint(books, total)
            if progress:
                progress(len(books), total)
            else:
                print(f"Đang quét: {len(books)}/{min(total, limit or total)}", end="\r")
            if not batch or len(books) >= total:
                break
            page += 1
        if not progress:
            print(" " * 60, end="\r")
        return books, total

    def get_catalog_book(self, book_id: int) -> CatalogBook:
        return self._to_catalog_book(
            self.http.get_json(f"{self.api_url}/books/{book_id}")
        )

    def download(self, book_id: int, file_format: str, directory: Path) -> Path:
        book = self.get_catalog_book(book_id)
        normalized_format = file_format.upper().lstrip(".")
        if normalized_format not in book.formats:
            available = ", ".join(book.formats) or "không có"
            raise CrawlerError(
                f"'{book.title}' không có {normalized_format}. Định dạng có sẵn: {available}."
            )
        url = f"{self.api_url}/files/book/{book.id}/{quote(normalized_format.lower())}"
        content = self.http.get_bytes(url)
        book_slug = slugify(book.title)
        book_directory = directory / f"{book_slug}-{book.id}"
        book_directory.mkdir(parents=True, exist_ok=True)
        destination = book_directory / f"{book_slug}.{normalized_format.lower()}"
        destination.write_bytes(content)
        return destination.resolve()

    def book_id_from_input(self, value: str) -> int | None:
        value = value.strip()
        if value.isdigit():
            return int(value)
        if not value.startswith(("http://", "https://")):
            return None
        parsed = urlparse(value)
        if parsed.hostname not in {"khosach.mnd.vn", "www.khosach.mnd.vn"}:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[-1].isdigit():
            return int(parts[-1])
        return None

    def _to_catalog_book(self, item: dict) -> CatalogBook:
        return CatalogBook(
            id=int(item["id"]),
            title=str(item.get("title") or "Không tên"),
            authors=[author["name"] for author in item.get("authors", [])],
            formats=[str(value).upper() for value in item.get("file_formats", [])],
            categories=[
                str(tag["name"])
                for tag in item.get("tags", [])
                if tag.get("name")
            ],
            cover_url=str(item.get("cover_url") or ""),
            source_url=f"{self.base_url}/book/{item['id']}",
        )

    def _to_book(self, item: dict | CatalogBook) -> Book:
        book = item if isinstance(item, CatalogBook) else self._to_catalog_book(item)
        return Book(
            title=book.title,
            url=book.source_url,
            author=", ".join(book.authors),
            cover_url=book.cover_url,
        )
