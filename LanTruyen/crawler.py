from __future__ import annotations

import json
import os
import sys
import re
import zlib
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from Config.accounts import AccountStore
from Config.settings import Settings
from Services.base_crawler import BaseCrawler
from Services.catalog import CatalogBook
from Services.errors import CrawlerError, ParseError
from Services.http_client import HttpClient
from Services.models import Book, Chapter


class LanTruyenCrawler(BaseCrawler):
    key, name, base_url = "lantruyen", "Làn Truyện", "https://lantruyen.vn"
    download_mode = "chapters"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.http = HttpClient(settings)
        self.accounts = AccountStore(settings.config_dir, legacy_path=settings.output_dir / "config" / "accounts.json")
        saved = self.accounts.get(self.key)
        cookie = str(saved.get("cookie") or "")
        if cookie:
            self.http.set_cookie_header(cookie, ".lantruyen.vn")
        self._resolved_urls: dict[int, str] = {}

    auth_mode = "session_cookie"

    def account_status(self) -> dict[str, str | bool]:
        account = self.accounts.get(self.key)
        return {
            "logged_in": bool(account.get("cookie")),
            "username": str(account.get("username") or "Phiên Làn Truyện"),
        }

    def open_login_browser(self) -> None:
        import webbrowser

        if not webbrowser.open(f"{self.base_url}/dang-nhap.html", new=2):
            raise CrawlerError("Không mở được Chrome mặc định.")

    def import_browser_session(self) -> dict:
        try:
            import browser_cookie3
        except ImportError as error:
            raise CrawlerError(
                "Thiếu browser-cookie3. Hãy chạy: python -m pip install -r requirements.txt"
            ) from error
        if sys.platform == "win32":
            root = Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        else:
            root = Path.home() / ".config" / "google-chrome"
        state_path = root / "Local State"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            profile_name = str(state.get("profile", {}).get("last_used") or "Default")
            profile_dir = root / profile_name
            cookie_path = profile_dir / "Network" / "Cookies"
            if not cookie_path.exists():
                cookie_path = profile_dir / "Cookies"
            jar = browser_cookie3.chrome(
                cookie_file=str(cookie_path),
                key_file=str(state_path),
                domain_name="lantruyen.vn",
            )
        except Exception as error:
            raise CrawlerError(
                "Không đọc được cookie từ profile Chrome đang dùng gần nhất. "
                "Hãy đóng Chrome rồi thử lại hoặc dùng Dán Cookie thủ công."
            ) from error
        cookie = "; ".join(
            f"{item.name}={item.value}" for item in jar
            if item.name and item.value and item.domain.endswith("lantruyen.vn")
        )
        if not cookie:
            raise CrawlerError(
                f"Profile Chrome {profile_name} chưa có phiên Làn Truyện. "
                "Hãy đăng nhập xong rồi thử lại."
            )
        return self.login(cookie)

    def login(self, cookie: str, password: str = "") -> dict:
        cookie = cookie.strip()
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        candidate = HttpClient(self.settings)
        candidate.set_cookie_header(cookie, ".lantruyen.vn")
        soup = BeautifulSoup(candidate.get_text(self.base_url), "html.parser")
        if soup.select_one(".mdv-header-not-login"):
            raise CrawlerError("Cookie không hợp lệ, đã hết hạn hoặc chưa đăng nhập Làn Truyện.")
        profile = soup.select_one(".mdv-header-box-login img[alt], .mdv-header-box-login [class*=name]")
        username = profile.get_text(" ", strip=True) if profile and profile.name != "img" else "Phiên Làn Truyện"
        account = {"username": username or "Phiên Làn Truyện", "cookie": cookie}
        self.accounts.save(self.key, account)
        self.http = candidate
        return account

    def logout(self) -> None:
        self.accounts.remove(self.key)
        self.http = HttpClient(self.settings)

    def search(self, keyword: str) -> list[Book]:
        data = self.http.post_form_json(
            f"{self.base_url}/sources/ajax/mongdaovien/tim-kiem-truyen.php",
            {"keyword": keyword},
        )
        soup, result = BeautifulSoup(data.get("html", ""), "html.parser"), []
        for link in soup.select('a[href*=".html"]'):
            title = link.select_one(".mdv-header-find-form-name")
            if title:
                author = link.select_one(".mdv-header-find-form-author span")
                image = link.select_one("img")
                result.append(Book(
                    title.get_text(" ", strip=True),
                    urljoin(self.base_url, link.get("href", "")),
                    author.get_text(" ", strip=True) if author else "",
                    cover_url=urljoin(self.base_url, image.get("src", "")) if image else "",
                ))
        return result

    def get_book(self, url: str) -> Book:
        clean = self._book_url(url)
        soup = self._soup(clean)
        canonical, title = soup.select_one('link[rel="canonical"]'), soup.select_one("h1")
        if not title:
            raise ParseError(f"Không đọc được tên truyện từ {url}")
        author = soup.select_one('a[href*="/tac-gia/"]')
        desc = soup.select_one(".lt-sps-v2__intro")
        cover = soup.select_one('meta[property="og:image"]')
        book_url = str(canonical.get("href") or clean) if canonical else clean
        match = re.search(r"id_truyen\s*:\s*['\"]?(\d+)", str(soup))
        if not match:
            raise ParseError("Không đọc được ID truyện Làn Truyện.")
        return Book(
            title.get_text(" ", strip=True), book_url,
            author.get_text(" ", strip=True) if author else "",
            desc.get_text("\n", strip=True) if desc else "",
            str(cover.get("content") or "") if cover else "",
            self._chapters(soup, int(match.group(1)), book_url),
        )

    def get_chapter(self, url: str) -> Chapter:
        soup = self._soup(url)
        if soup.select_one(".mdv-chapter-login-card"):
            raise CrawlerError(
                "Phiên Làn Truyện chưa đăng nhập hoặc đã hết hạn. "
                "Hãy cập nhật Cookie trong mục Cấu hình."
            )
        match = re.search(r"[?&]chuong=(\d+)", url)
        number = int(match.group(1)) if match else None
        content = soup.select_one("#noi_dung_truyen")
        text = content.get_text(" ", strip=True) if content else ""
        if not text or "Đang tải nội dung chương" in text:
            source = str(soup)
            fields = {}
            patterns = {
                "id_truyen": r"id_truyen:\s*(\d+)",
                "id_chapter": r"id_chapter:\s*(\d+)",
                "so_thu_tu": r"so_thu_tu:\s*(\d+)",
                "pnvn_token": r"token:\s*['\"]([^'\"]+)",
            }
            for key, pattern in patterns.items():
                value = re.search(pattern, source)
                if not value:
                    raise ParseError(f"Không đọc được cấu hình nội dung chương từ {url}")
                fields[key] = value.group(1)
            payload = self.http.post_form_json(
                f"{self.base_url}/sources/ajax/mongdaovien/doc-noi-dung-chuong.php",
                fields,
            )
            if payload.get("status") != "success":
                message = str(payload.get("message") or "")
                code = str(payload.get("code") or "")
                if code == "login_required":
                    raise CrawlerError(
                        "Phiên Làn Truyện đã hết hạn. Hãy cập nhật Cookie trong mục Cấu hình."
                    )
                raise CrawlerError(message or "Tài khoản chưa có quyền đọc chương này.")
            html = "".join(payload.get("chunks") or []) or str(payload.get("html") or "")
            content = BeautifulSoup(html, "html.parser")
            for line in content.select(".mdv-obf-line"):
                line.string = str(line.get("data-mdv-1") or "") + str(line.get("data-mdv-2") or "")
        if not content or not content.get_text(" ", strip=True):
            raise ParseError(f"Không đọc được nội dung chương từ {url}")
        return Chapter(
            f"Chương {number or ''}", url, content.get_text("\n", strip=True), number
        )

    def book_id_from_input(self, value: str) -> int | str | None:
        value = value.strip()
        if value.isdigit():
            return int(value)
        try:
            return self._book_url(value)
        except CrawlerError:
            return None

    def get_catalog_book(self, value: int | str) -> CatalogBook:
        if not isinstance(value, str):
            value = self._resolved_urls.get(value, "")
        if not value:
            raise CrawlerError("Làn Truyện cần URL truyện đầy đủ để tải.")
        book = self.get_book(value)
        self._resolved_urls[self._stable_id(book.url)] = book.url
        return CatalogBook(
            id=self._stable_id(book.url), title=book.title,
            authors=[book.author] if book.author else [], formats=["TXT"],
            cover_url=book.cover_url, source_url=book.url,
        )

    def download(
        self, book_id: int | str, file_format: str, directory: Path,
        start: int = 1, limit: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        if not isinstance(book_id, str):
            book_id = self._resolved_urls.get(book_id, "")
        if not book_id:
            raise CrawlerError("Làn Truyện cần URL truyện đầy đủ để tải.")
        if file_format.upper().lstrip(".") != "TXT":
            raise CrawlerError("Làn Truyện chỉ hỗ trợ định dạng TXT.")
        if start < 1 or (limit is not None and limit < 1):
            raise CrawlerError("Chương bắt đầu và số chương phải lớn hơn hoặc bằng 1.")
        book = self.get_book(book_id)
        selected = book.chapters[start - 1:]
        if limit is not None:
            selected = selected[:limit]
        if not selected:
            raise CrawlerError("Không có chương nào trong khoảng đã chọn.")
        book_dir = directory / f"lantruyen-{self._stable_id(book.url)}"
        chapter_dir = book_dir / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        (book_dir / "book.json").write_text(json.dumps({
            "title": book.title, "author": book.author, "source_url": book.url,
            "total_chapters": len(book.chapters),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        for index, link in enumerate(selected, 1):
            chapter = self.get_chapter(link.url)
            number = chapter.number or start + index - 1
            (chapter_dir / f"chapter-{number:05d}.txt").write_text(
                f"{chapter.title}\n{chapter.url}\n\n{chapter.content}\n", encoding="utf-8"
            )
            if progress:
                progress(index, len(selected))
        return book_dir.resolve()

    def _chapters(self, soup, story_id: int, book_url: str) -> list[Chapter]:
        box, found = soup.select_one(".mvd-san-pham-show-dsc-content"), {}
        pages = int(box.get("data-total-trang", 1)) if box else 1
        for page in range(1, pages + 1):
            page_soup = soup
            if page > 1:
                query = urlencode({"id_truyen": story_id, "page": page, "per_page": 10})
                data = self.http.get_json(
                    f"{self.base_url}/sources/ajax/mongdaovien/load-chuong-page.php?{query}"
                )
                page_soup = BeautifulSoup(data.get("html", ""), "html.parser")
            for row in page_soup.select(".mvd-sps-chuong-row"):
                title = row.select_one(".mvd-sps-chuong-name")
                match = re.search(r"Chương\s+(\d+)", title.get_text() if title else "", re.I)
                if match:
                    number = int(match.group(1))
                    found[number] = Chapter(
                        title.get_text(" ", strip=True),
                        f"{book_url}?chuong={number}", number=number,
                    )
        if not found:
            raise ParseError(f"Không đọc được danh sách chương từ {book_url}")
        return [found[number] for number in sorted(found)]

    def _soup(self, url: str):
        return BeautifulSoup(self.http.get_text(url), "html.parser")

    def _book_url(self, value: str) -> str:
        parsed = urlparse(value)
        if parsed.hostname not in {"lantruyen.vn", "www.lantruyen.vn"} or not parsed.path.endswith(".html"):
            raise CrawlerError("URL truyện Làn Truyện không hợp lệ.")
        return f"{self.base_url}{parsed.path}"

    @staticmethod
    def _stable_id(url: str) -> int:
        return zlib.crc32(url.rstrip("/").encode("utf-8")) & 0xFFFFFFFF
