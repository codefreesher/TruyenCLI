from __future__ import annotations

import curses
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from Config.settings import Settings
from Config.preferences import DownloadPreferences, remove_local_download
from Services.catalog import CatalogBook, CatalogStore, normalize_search, scan_and_save
from Services.errors import CrawlerError
from Services.google_drive import GoogleDriveService


T = TypeVar("T")


def is_enter_key(key: int | str) -> bool:
    """Nhận Enter từ terminal thường, keypad và terminal tích hợp IDE."""
    if key in (10, 13, "\n", "\r", curses.KEY_ENTER):
        return True
    if not isinstance(key, int):
        return False
    try:
        return curses.keyname(key) in (b"^J", b"^M", b"KEY_ENTER")
    except curses.error:
        return False


class BackRequested(Exception):
    """Người dùng muốn quay lại màn hình trước."""


@dataclass(frozen=True, slots=True)
class Action:
    label: str
    description: str
    handler: str


ACTIONS = (
    Action("Tổng quan", "Xem trạng thái nguồn và nơi lưu dữ liệu.", "overview"),
    Action("Quét catalog", "Đọc danh mục website và ghi lại thành JSON.", "scan"),
    Action("Thư viện", "Tìm và duyệt những sách đã quét.", "catalog"),
    Action("Tải sách", "Chọn sách và định dạng ebook cần tải.", "download"),
    Action("Tìm online", "Tìm trực tiếp trên website nguồn.", "search"),
    Action("Cấu hình", "Đăng nhập hoặc xóa phiên tài khoản nguồn.", "config"),
)


class TuiApp:
    def __init__(self, screen, settings: Settings, registry) -> None:
        self.screen = screen
        self.settings = settings
        self.registry = registry
        self.drive = GoogleDriveService(settings)
        self.preferences = DownloadPreferences(settings)
        self.cursor = 0
        self.title = "Sẵn sàng"
        self.lines = ["Chọn một chức năng ở cột bên trái để bắt đầu."]
        self.error = False
        self.running = True

    def run(self) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        curses.start_color()
        curses.init_pair(1, curses.COLOR_YELLOW, -1)
        curses.init_pair(2, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        self.screen.keypad(True)
        # Yêu cầu terminal đánh dấu nội dung paste để không nhầm Esc với quay lại.
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
        try:
            while self.running:
                self.draw()
                key = self.screen.getch()
                if key in (ord("q"), 3):
                    self.running = False
                elif key in (curses.KEY_UP, ord("k")):
                    self.cursor = (self.cursor - 1) % len(ACTIONS)
                elif key in (curses.KEY_DOWN, ord("j")):
                    self.cursor = (self.cursor + 1) % len(ACTIONS)
                elif is_enter_key(key):
                    self.execute(ACTIONS[self.cursor].handler)
                elif key == curses.KEY_RESIZE:
                    continue
        finally:
            sys.stdout.write("\x1b[?2004l")
            sys.stdout.flush()

    def draw(self) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 15 or width < 64:
            self.safe_add(1, 2, "Terminal quá nhỏ. Cần tối thiểu 64x15.", curses.A_BOLD)
            self.screen.refresh()
            return
        self.safe_add(1, 3, "TRUYỆN CLI", curses.A_BOLD | curses.color_pair(1))
        self.safe_add(1, width - 20, "● Sẵn sàng", curses.color_pair(3))
        self.hline(2, 1, width - 2)

        sidebar_width = min(27, max(22, width // 4))
        self.safe_add(4, 3, "CHỨC NĂNG", curses.A_BOLD | curses.color_pair(1))
        for index, action in enumerate(ACTIONS):
            marker = "› " if index == self.cursor else "  "
            style = curses.A_REVERSE | curses.A_BOLD if index == self.cursor else 0
            self.safe_add(6 + index * 2, 3, f"{marker}{action.label}".ljust(sidebar_width - 4), style)

        for row in range(3, height - 2):
            self.safe_add(row, sidebar_width, "│", curses.color_pair(1))
        content_x = sidebar_width + 3
        content_width = width - content_x - 3
        title_style = curses.A_BOLD | curses.color_pair(4 if self.error else 2)
        self.safe_add(4, content_x, self.title, title_style)
        row = 6
        for line in self.lines:
            wrapped = textwrap.wrap(str(line), max(10, content_width)) or [""]
            for part in wrapped:
                if row >= height - 3:
                    self.safe_add(row, content_x, "…", curses.color_pair(1))
                    break
                self.safe_add(row, content_x, part)
                row += 1
            if row >= height - 3:
                break
        self.hline(height - 2, 1, width - 2)
        self.safe_add(
            height - 1,
            3,
            "↑/↓ hoặc j/k: di chuyển   Enter: chọn   q/Ctrl+C: thoát",
            curses.color_pair(1),
        )
        self.screen.refresh()

    def execute(self, handler: str) -> None:
        try:
            getattr(self, f"action_{handler}")()
            self.error = False
        except CrawlerError as error:
            self.title = "Có lỗi"
            self.lines = [str(error)]
            self.error = True
        except BackRequested:
            self.title = "Sẵn sàng"
            self.lines = ["Chọn một chức năng ở cột bên trái để bắt đầu."]
            self.error = False
        except (KeyboardInterrupt, EOFError):
            self.title = "Đã hủy"
            self.lines = ["Thao tác đã được hủy."]

    def action_overview(self) -> None:
        self.title = "Tổng quan"
        self.lines = [
            f"Thư mục dữ liệu: {self.settings.output_dir.resolve()}",
            f"Python đang dùng: {sys.executable}",
            "",
            "Nguồn đã đăng ký:",
        ]
        for crawler in self.registry.all():
            store = CatalogStore(self.settings.output_dir, crawler.key)
            state = "đã có catalog" if store.path.exists() else "chưa quét"
            account = (
                crawler.account_status() if hasattr(crawler, "account_status") else {}
            )
            login_state = (
                f"đã đăng nhập: {account.get('username') or 'tài khoản'}"
                if account.get("logged_in")
                else "chưa đăng nhập"
            )
            self.lines.append(
                f"  ● {crawler.name} · {state} · {login_state} · {crawler.base_url}"
            )

    def action_scan(self) -> None:
        crawler = self.choose_source("scan_catalog")
        limit_text = self.prompt("Số sách cần quét", "Để trống để quét tất cả")
        if limit_text and not limit_text.isdigit():
            raise CrawlerError("Số lượng phải là số nguyên dương.")
        limit = int(limit_text) if limit_text else None

        def progress(done: int, total: int) -> None:
            self.title = "Đang quét catalog"
            self.lines = [f"{crawler.name}: {done}/{min(total, limit or total)} sách"]
            self.draw()

        saved, total, path = scan_and_save(
            crawler, CatalogStore(self.settings.output_dir, crawler.key), limit, progress
        )
        self.title = "Quét hoàn tất"
        self.lines = [f"Đã lưu {saved}/{total} sách.", str(path)]

    def action_catalog(self) -> None:
        crawler = self.choose_source("scan_catalog")
        query = self.prompt("Tìm trong thư viện", "Tên sách hoặc tác giả; để trống để duyệt")
        store = CatalogStore(self.settings.output_dir, crawler.key)
        category = self.choose_category(store)
        books = store.search(query, 200, category=category)
        if not books:
            raise CrawlerError("Không tìm thấy sách phù hợp trong catalog.")
        selected = self.select("Thư viện đã quét", books, self.book_label)
        self.title = selected.title
        self.lines = [
            f"ID: {selected.id}",
            f"Tác giả: {', '.join(selected.authors) or 'Chưa rõ'}",
            f"Thể loại: {', '.join(selected.categories) or 'Chưa phân loại'}",
            f"Nguồn: {selected.source_url}",
            "",
            "Dùng mục Tải sách để chọn định dạng và tải file.",
        ]

    def action_download(self) -> None:
        crawler = self.choose_source("download")
        store = CatalogStore(self.settings.output_dir, crawler.key)
        while True:
            query = self.prompt(
                "Chọn sách",
                "Nhập ID, link, tên sách hoặc tác giả · Enter để xem danh sách",
            )
            book_id = (
                crawler.book_id_from_input(query)
                if hasattr(crawler, "book_id_from_input")
                else (int(query) if query.isdigit() else None)
            )
            if book_id is not None:
                self.title = "Đang đọc thông tin sách"
                self.lines = [f"ID: {book_id}"]
                self.draw()
                books = [crawler.get_catalog_book(book_id)]
                if self.download_from_results(crawler, books):
                    return
            else:
                try:
                    while True:
                        category = self.choose_category(store)
                        books = store.search(query, limit=None, category=category)
                        if not books:
                            self.show_message(
                                "Không tìm thấy",
                                "Không có sách phù hợp trong thể loại này.",
                            )
                            continue
                        if category is not None:
                            mode = self.select(
                                f"Cách tải · {category}",
                                ("Chọn một truyện", f"Tải tất cả {len(books)} truyện"),
                                lambda value: value,
                                details=(f"Thể loại: {category}",),
                            )
                            if mode.startswith("Tải tất cả"):
                                if self.download_category(crawler, books, category):
                                    return
                                continue
                        if self.download_from_results(crawler, books):
                            return
                except BackRequested:
                    continue

    def download_category(
        self, crawler, books: list[CatalogBook], category: str
    ) -> bool:
        """Tải lần lượt toàn bộ kết quả thuộc một thể loại."""
        known_formats = sorted(
            {value.upper() for book in books for value in book.formats}
        )
        if crawler.key == "khosach":
            known_formats = sorted(set(known_formats) | {"PDF", "EPUB", "MOBI", "AZW3"})
        elif getattr(crawler, "download_mode", "") == "chapters":
            known_formats = ["TXT"]
        choices = ["Tự động (định dạng đầu tiên)"]
        choices.extend(
            f"Ưu tiên {value} (không có sẽ dùng định dạng đầu tiên)"
            for value in known_formats
        )
        selected_format = self.select(
            f"Định dạng tải hàng loạt · {category}", choices, lambda value: value
        )
        preferred = ""
        if selected_format.startswith("Ưu tiên "):
            preferred = selected_format.removeprefix("Ưu tiên ").split(" ", 1)[0].upper()

        common_args: dict = {}
        if getattr(crawler, "download_mode", "") == "chapters":
            start_text = self.prompt(
                "Chương bắt đầu cho mỗi truyện", "Để trống để bắt đầu từ chương 1"
            )
            limit_text = self.prompt(
                "Số chương của mỗi truyện",
                "Để trống để tải đến chương cuối của từng truyện",
            )
            if start_text and (not start_text.isdigit() or int(start_text) < 1):
                raise CrawlerError("Chương bắt đầu phải là số nguyên dương.")
            if limit_text and (not limit_text.isdigit() or int(limit_text) < 1):
                raise CrawlerError("Số chương cần tải phải là số nguyên dương.")
            common_args = {
                "start": int(start_text or "1"),
                "limit": int(limit_text) if limit_text else None,
            }

        completed = 0
        failed: list[str] = []
        drive_enabled = self.drive.status()["enabled"]
        for position, item in enumerate(books, 1):
            try:
                self.title = f"Tải thể loại · {category}"
                self.lines = [
                    f"Truyện {position}/{len(books)}",
                    item.title,
                    f"Thành công: {completed} · Lỗi: {len(failed)}",
                ]
                self.draw()
                detail = crawler.get_catalog_book(item.id)
                if not detail.formats:
                    raise CrawlerError("Không có định dạng tải.")
                file_format = (
                    preferred if preferred in detail.formats else detail.formats[0]
                )
                download_args = dict(common_args)
                if getattr(crawler, "download_mode", "") == "chapters":
                    def update_progress(done: int, total: int, title=detail.title) -> None:
                        self.title = f"Tải thể loại · {category}"
                        self.lines = [
                            f"Truyện {position}/{len(books)} · {title}",
                            f"Chương {done}/{total}",
                            f"Thành công: {completed} · Lỗi: {len(failed)}",
                        ]
                        self.draw()

                    download_args["progress"] = update_progress
                destination = crawler.download(
                    detail.id,
                    file_format,
                    self.settings.output_dir / "downloads" / crawler.key,
                    **download_args,
                )
                if drive_enabled:
                    try:
                        drive_url = self.drive.upload_download(
                            destination, crawler.key, detail.title, detail.id
                        )
                        if drive_url and not self.preferences.keep_local:
                            remove_local_download(destination)
                    except CrawlerError as error:
                        failed.append(f"{detail.title}: Drive: {error}")
                        # Tránh lặp lại cùng một lỗi Drive cho hàng nghìn truyện.
                        drive_enabled = False
                completed += 1
            except CrawlerError as error:
                failed.append(f"{item.title}: {error}")

        details = [
            f"Thể loại: {category}",
            f"Đã tải local: {completed}/{len(books)} truyện",
            f"Lỗi: {len(failed)}",
        ]
        details.extend(failed[:3])
        if len(failed) > 3:
            details.append(f"… và {len(failed) - 3} lỗi khác")
        try:
            next_step = self.select(
                "Tải thể loại hoàn tất",
                ("Chọn thể loại khác", "Về menu chính"),
                lambda value: value,
                details=details,
            )
        except BackRequested:
            return False
        return next_step == "Về menu chính"

    def download_from_results(self, crawler, books: list[CatalogBook]) -> bool:
        """Tải từ một danh sách; False nghĩa là quay lại màn hình trước."""
        while True:
            try:
                selected = self.select("Chọn sách cần tải", books, self.book_label)
            except BackRequested:
                return False

            self.title = "Đang đọc định dạng"
            self.lines = [selected.title]
            self.draw()
            detail = crawler.get_catalog_book(selected.id)
            if not detail.formats:
                self.show_message(
                    "Không có file", f"'{detail.title}' hiện không có file để tải."
                )
                continue

            while True:
                try:
                    file_format = self.select(
                        f"Định dạng · {detail.title}",
                        detail.formats,
                        lambda value: value,
                    )
                except BackRequested:
                    # Quay lại đúng danh sách kết quả vừa lọc.
                    break
                self.title = "Đang tải"
                self.lines = [f"{detail.title} ({file_format})"]
                self.draw()
                download_args = {}
                if getattr(crawler, "download_mode", "") == "chapters":
                    start_text = self.prompt(
                        "Chương bắt đầu", "Để trống để bắt đầu từ chương 1"
                    )
                    limit_text = self.prompt(
                        "Số chương cần tải",
                        "Để trống để tải từ chương bắt đầu đến chương cuối",
                    )
                    if start_text and not start_text.isdigit():
                        raise CrawlerError("Chương bắt đầu phải là số nguyên dương.")
                    if limit_text and not limit_text.isdigit():
                        raise CrawlerError("Số chương cần tải phải là số nguyên dương.")
                    download_args["start"] = int(start_text or "1")
                    download_args["limit"] = int(limit_text) if limit_text else None

                    def update_progress(done: int, total: int) -> None:
                        self.title = "Đang tải chương"
                        self.lines = [detail.title, f"{done}/{total} chương"]
                        self.draw()

                    download_args["progress"] = update_progress
                destination = crawler.download(
                    detail.id, file_format,
                    self.settings.output_dir / "downloads" / crawler.key,
                    **download_args,
                )
                drive_url = None
                drive_error = None
                if self.drive.status()["enabled"]:
                    self.title = "Đang tải lên Google Drive"
                    self.lines = [detail.title, str(destination)]
                    self.draw()
                    try:
                        drive_url = self.drive.upload_download(
                            destination, crawler.key, detail.title, detail.id
                        )
                    except CrawlerError as error:
                        # Sách local đã tải xong; lỗi Drive chỉ là cảnh báo.
                        drive_error = str(error)
                completed_details = [detail.title, str(destination)]
                if drive_url:
                    completed_details.append(f"Drive: {drive_url}")
                    if not self.preferences.keep_local:
                        removed = remove_local_download(destination)
                        completed_details.append(f"Đã xóa bản local: {removed}")
                elif drive_error:
                    completed_details.append(f"Cảnh báo Drive: {drive_error}")
                try:
                    next_step = self.select(
                        "Tải hoàn tất",
                        ("Tải định dạng khác", "Chọn sách khác", "Về menu chính"),
                        lambda value: value,
                        details=completed_details,
                    )
                except BackRequested:
                    next_step = "Chọn sách khác"
                if next_step == "Tải định dạng khác":
                    continue
                if next_step == "Về menu chính":
                    return True
                break

    def action_search(self) -> None:
        crawler = self.choose_source()
        query = self.prompt("Tìm online", "Nhập hoặc dán link, tên sách hay tác giả")
        if not query:
            raise CrawlerError("Từ khóa không được để trống.")
        self.title = "Đang tìm kiếm"
        self.lines = [f"{crawler.name}: {query}"]
        self.draw()
        book_id = (
            crawler.book_id_from_input(query)
            if hasattr(crawler, "book_id_from_input")
            else None
        )
        if book_id is not None and hasattr(crawler, "get_catalog_book"):
            detail = crawler.get_catalog_book(book_id)
            self.title = f"Kết quả · {crawler.name}"
            self.lines = [
                detail.title,
                f"ID: {detail.id}",
                f"Tác giả: {', '.join(detail.authors) or 'Chưa rõ'}",
                f"Thể loại: {', '.join(detail.categories) or 'Chưa phân loại'}",
                f"Định dạng: {', '.join(detail.formats) or 'Chưa có'}",
                detail.source_url,
            ]
            return
        books = crawler.search(query)
        if not books:
            raise CrawlerError("Không tìm thấy kết quả.")
        self.title = f"Kết quả · {crawler.name}"
        self.lines = []
        for index, book in enumerate(books[:50], 1):
            self.lines.extend((f"{index}. {book.title}", f"   {book.url}"))

    def action_config(self) -> None:
        selected = self.select(
            "Cấu hình",
            ("Google Drive", "Lưu file trên máy", "Tài khoản nguồn website", "Quay lại"),
            lambda value: value,
        )
        if selected == "Quay lại":
            return
        if selected == "Google Drive":
            self.configure_google_drive()
            return
        if selected == "Lưu file trên máy":
            self.configure_local_storage()
            return
        self.configure_source_account()

    def configure_local_storage(self) -> None:
        keep_local = self.preferences.keep_local
        actions = ["Bật lưu local" if not keep_local else "Tắt lưu local"]
        if not keep_local:
            actions.append("Đồng bộ và xóa bản local hiện có")
        actions.append("Quay lại")
        selected = self.select(
            "Cấu hình · Lưu file trên máy",
            actions,
            lambda value: value,
            details=(
                f"Trạng thái: {'Đang bật' if keep_local else 'Đang tắt'}",
                "Khi tắt, bản local chỉ bị xóa sau khi tải Drive thành công.",
            ),
        )
        if selected == "Quay lại":
            return
        if selected == "Đồng bộ và xóa bản local hiện có":
            self.sync_local_downloads()
            return
        enabling = selected == "Bật lưu local"
        if not enabling and not self.drive.status()["enabled"]:
            raise CrawlerError("Hãy bật và cấu hình Google Drive trước khi tắt lưu local.")
        self.preferences.set_keep_local(enabling)
        if not enabling:
            self.sync_local_downloads()
            return
        self.show_message(
            "Đã cập nhật",
            f"Lưu file trên máy: {'Bật' if enabling else 'Tắt'}.",
        )

    def configure_source_account(self) -> None:
        crawler = self.choose_source("account_status")
        while True:
            status = crawler.account_status()
            username = str(status.get("username") or "")
            state = f"Đã đăng nhập: {username}" if status.get("logged_in") else "Chưa đăng nhập"
            auth_hint = (
                "Mở Chrome đang dùng, đăng nhập Google/Zalo; phiên được nhập và lưu mã hóa."
                if getattr(crawler, "auth_mode", "") == "session_cookie"
                else "Mật khẩu chỉ dùng để đăng nhập, không được lưu."
            )
            actions = (
                ["Mở Chrome có sẵn để đăng nhập", "Dán Cookie thủ công"]
                if getattr(crawler, "auth_mode", "") == "session_cookie"
                else ["Đăng nhập tài khoản"]
            )
            if status.get("logged_in"):
                actions.append("Đăng xuất và xóa token")
            actions.append("Quay lại")
            selected = self.select(
                f"Cấu hình · {crawler.name}",
                actions,
                lambda value: value,
                details=(state, auth_hint),
            )
            if selected == "Quay lại":
                return
            if selected == "Đăng xuất và xóa token":
                crawler.logout()
                self.show_message("Đã đăng xuất", f"Đã xóa token của {crawler.name}.")
                continue
            if selected == "Mở Chrome có sẵn để đăng nhập":
                crawler.open_login_browser()
                self.show_message(
                    "Đăng nhập trên Chrome",
                    "Đăng nhập Google hoặc Zalo, sau đó quay lại đây và nhấn phím bất kỳ.",
                )
                self.title = "Đang nhập phiên từ Chrome"
                self.lines = [crawler.name]
                self.draw()
                account = crawler.import_browser_session()
                self.show_message(
                    "Đăng nhập thành công",
                    f"Đã lưu phiên mã hóa: {account.get('username') or 'Làn Truyện'}",
                )
                continue
            if selected == "Dán Cookie thủ công":
                cookie = self.prompt(
                    "Cookie phiên Làn Truyện",
                    "Đăng nhập Google/Zalo trên trình duyệt, rồi dán toàn bộ Cookie request.",
                    secret=True,
                )
                self.title = "Đang kiểm tra phiên đăng nhập"
                self.lines = [crawler.name]
                self.draw()
                account = crawler.login(cookie, "")
                self.show_message(
                    "Đăng nhập thành công",
                    f"Đã lưu phiên mã hóa: {account.get('username') or 'Làn Truyện'}",
                )
                continue
            username = self.prompt(
                "Tên đăng nhập",
                "Kho Sách sử dụng email hoặc tên tài khoản đã đăng ký.",
            )
            password = self.prompt(
                "Mật khẩu",
                "Mật khẩu không hiển thị và không được lưu vào file.",
                secret=True,
            )
            self.title = "Đang đăng nhập"
            self.lines = [crawler.name, username]
            self.draw()
            crawler.login(username, password)
            self.show_message("Đăng nhập thành công", f"Tài khoản: {username}")

    def configure_google_drive(self) -> None:
        while True:
            status = self.drive.status()
            connection = "Đã đăng nhập OAuth" if status["connected"] else "Chưa đăng nhập OAuth"
            automatic = "Bật" if status["enabled"] else "Tắt"
            folder = status["folder_url"] or "Chưa đặt"
            actions = ["Đăng nhập OAuth", "Đặt link thư mục lưu"]
            if status["connected"] and status["folder_id"]:
                actions.append("Tắt tự tải lên" if status["enabled"] else "Bật tự tải lên")
                actions.append("Đồng bộ sách local lên Drive")
            if status["connected"]:
                actions.append("Đăng xuất và xóa token")
            actions.append("Quay lại")
            selected = self.select(
                "Cấu hình · Google Drive",
                actions,
                lambda value: value,
                details=(connection, f"Tự tải lên: {automatic}", f"Thư mục: {folder}"),
            )
            if selected == "Quay lại":
                return
            if selected == "Đăng nhập OAuth":
                path = self.prompt(
                    "File OAuth Client",
                    "Nhập đường dẫn client_secret.json (loại Desktop app)",
                )
                self.title = "Đang chờ đăng nhập Google"
                self.lines = ["Trình duyệt sẽ mở để bạn cấp quyền Google Drive."]
                self.draw()
                self.drive.connect(path)
                self.show_message("Google Drive", "Đăng nhập OAuth thành công.")
            elif selected == "Đặt link thư mục lưu":
                folder_url = self.prompt(
                    "Thư mục Google Drive",
                    "Dán link thư mục sẽ nhận sách đã tải",
                )
                folder_id = self.drive.set_folder(folder_url)
                self.show_message("Đã lưu thư mục Drive", f"Folder ID: {folder_id}")
            elif selected == "Đăng xuất và xóa token":
                self.drive.disconnect()
                self.show_message("Google Drive", "Đã xóa token OAuth trên máy.")
            elif selected == "Đồng bộ sách local lên Drive":
                self.sync_local_downloads()
            else:
                self.drive.set_enabled(selected == "Bật tự tải lên")

    def sync_local_downloads(self) -> None:
        downloads = self.settings.output_dir / "downloads"
        entries: list[tuple[str, Path]] = []
        if downloads.exists():
            for source_dir in downloads.iterdir():
                if not source_dir.is_dir():
                    continue
                for item in source_dir.iterdir():
                    if item.is_dir() or item.is_file():
                        entries.append((source_dir.name, item))
        if not entries:
            self.show_message("Google Drive", "Không có sách local để đồng bộ.")
            return
        completed = 0
        failures: list[str] = []
        for position, (source, path) in enumerate(entries, 1):
            self.title = "Đồng bộ Google Drive"
            self.lines = [
                f"{position}/{len(entries)}",
                f"Nguồn: {source}",
                path.name,
                f"Thành công: {completed} · Lỗi: {len(failures)}",
            ]
            self.draw()
            try:
                drive_url = self.drive.upload_download(path, source)
                if drive_url:
                    completed += 1
                    if not self.preferences.keep_local:
                        remove_local_download(path)
            except CrawlerError as error:
                failures.append(f"{path.name}: {error}")
                # Lỗi quyền hoặc phiên thường ảnh hưởng toàn bộ phần còn lại.
                break
        message = f"Đã đồng bộ {completed}/{len(entries)} mục. Lỗi: {len(failures)}."
        if failures:
            message += f" {failures[0]}"
        self.show_message("Đồng bộ hoàn tất", message)

    def choose_source(self, capability: str | None = None):
        crawlers = [
            crawler for crawler in self.registry.all()
            if capability is None or hasattr(crawler, capability)
        ]
        return self.select("Chọn nguồn", crawlers, lambda item: f"{item.name}  ·  {item.base_url}")

    def choose_category(self, store: CatalogStore) -> str | None:
        counts = store.category_counts()
        if not counts:
            return None
        choices: list[tuple[str | None, int]] = [(None, len(store.load())), *counts]
        selected = self.select(
            "Chọn thể loại",
            choices,
            lambda item: f"{item[0] or 'Tất cả thể loại'}  ·  {item[1]} sách",
        )
        return selected[0]

    def select(
        self,
        title: str,
        items: Sequence[T],
        label: Callable[[T], str],
        details: Sequence[str] = (),
    ) -> T:
        if not items:
            raise CrawlerError("Không có lựa chọn phù hợp.")
        cursor = 0
        offset = 0
        query: list[str] = []
        while True:
            self.screen.erase()
            height, width = self.screen.getmaxyx()
            search_text = "".join(query)
            normalized_query = normalize_search(search_text)
            filtered = [
                index
                for index, item in enumerate(items)
                if not normalized_query
                or normalized_query in normalize_search(label(item))
            ]
            if cursor >= len(filtered):
                cursor = max(0, len(filtered) - 1)
            visible = max(1, height - 9)
            if cursor < offset:
                offset = cursor
            elif cursor >= offset + visible:
                offset = cursor - visible + 1
            self.safe_add(1, 3, title, curses.A_BOLD | curses.color_pair(1))
            detail_row = 2
            for detail in details:
                self.safe_add(detail_row, 3, detail, curses.color_pair(2), width - 6)
                detail_row += 1
            self.safe_add(
                detail_row,
                3,
                f"Tìm: {search_text}",
                curses.A_BOLD | curses.color_pair(2),
                width - 6,
            )
            self.safe_add(
                detail_row + 1,
                3,
                f"{len(filtered)}/{len(items)} lựa chọn",
                curses.color_pair(2),
            )
            list_start = detail_row + 3
            visible = max(1, height - list_start - 2)
            if cursor < offset:
                offset = cursor
            elif cursor >= offset + visible:
                offset = cursor - visible + 1
            self.hline(detail_row + 2, 1, width - 2)
            for row, filtered_index in enumerate(
                range(offset, min(len(filtered), offset + visible)), list_start
            ):
                item_index = filtered[filtered_index]
                text = ("› " if filtered_index == cursor else "  ") + label(items[item_index])
                style = curses.A_REVERSE | curses.A_BOLD if filtered_index == cursor else 0
                self.safe_add(row, 3, text, style, width - 6)
            if not filtered:
                self.safe_add(list_start, 3, "Không có kết quả phù hợp.", curses.color_pair(4))
            self.safe_add(
                height - 1,
                3,
                "Gõ để tìm   Backspace: xóa   ↑/↓: chọn   Enter: xác nhận   Esc: quay lại",
                curses.color_pair(1),
            )
            self.screen.refresh()
            key = self.screen.get_wch()
            if key == curses.KEY_UP:
                if filtered:
                    cursor = (cursor - 1) % len(filtered)
            elif key == curses.KEY_DOWN:
                if filtered:
                    cursor = (cursor + 1) % len(filtered)
            elif is_enter_key(key):
                if filtered:
                    return items[filtered[cursor]]
            elif key in (27, curses.KEY_LEFT, "\x1b"):
                if key == "\x1b":
                    pasted = self._read_bracketed_paste()
                    if pasted is not None:
                        query.extend(pasted)
                        cursor = offset = 0
                        continue
                raise BackRequested
            elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                if query:
                    query.pop()
                    cursor = offset = 0
            elif isinstance(key, str) and key.isprintable():
                query.append(key)
                cursor = offset = 0

    def prompt(self, title: str, hint: str = "", secret: bool = False) -> str:
        value: list[str] = []
        curses.curs_set(1)
        curses.noecho()
        try:
            while True:
                self.screen.erase()
                height, width = self.screen.getmaxyx()
                self.safe_add(2, 3, title, curses.A_BOLD | curses.color_pair(1))
                self.safe_add(4, 3, hint, curses.color_pair(2))
                text = "".join(value)
                displayed = "•" * len(value) if secret else text
                self.safe_add(
                    6, 3, f"› {displayed}", curses.A_BOLD | curses.color_pair(1), width - 6
                )
                self.safe_add(
                    height - 1,
                    3,
                    "Ctrl+Shift+V/chuột phải: dán   Enter: xác nhận   Esc: quay lại",
                    curses.color_pair(1),
                )
                try:
                    self.screen.move(6, min(width - 2, 5 + len(text)))
                except curses.error:
                    pass
                self.screen.refresh()
                key = self.screen.get_wch()
                if is_enter_key(key):
                    return text.strip()
                if key == "\x1b":
                    pasted = self._read_bracketed_paste()
                    if pasted is not None:
                        value.extend(pasted)
                        continue
                    raise BackRequested
                if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                    if value:
                        value.pop()
                elif isinstance(key, str) and key.isprintable():
                    value.append(key)
        finally:
            curses.noecho()
            curses.curs_set(0)

    def _read_bracketed_paste(self) -> str | None:
        """Đọc nội dung sau ESC[200~...ESC[201~ do terminal gửi khi paste."""
        self.screen.timeout(80)
        try:
            prefix: list[str] = []
            for _ in range(5):
                try:
                    key = self.screen.get_wch()
                except curses.error:
                    return None
                if not isinstance(key, str):
                    return None
                prefix.append(key)
            if "".join(prefix) != "[200~":
                return None
            content: list[str] = []
            suffix = "\x1b[201~"
            empty_reads = 0
            while True:
                try:
                    key = self.screen.get_wch()
                except curses.error:
                    empty_reads += 1
                    if empty_reads >= 10:
                        return "".join(content).replace("\r", " ").replace("\n", " ")
                    continue
                empty_reads = 0
                if isinstance(key, str):
                    content.append(key)
                    if "".join(content[-len(suffix):]) == suffix:
                        del content[-len(suffix):]
                        return "".join(content).replace("\r", " ").replace("\n", " ")
        finally:
            self.screen.timeout(-1)

    def show_message(self, title: str, message: str) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        self.safe_add(2, 3, title, curses.A_BOLD | curses.color_pair(1))
        for index, line in enumerate(textwrap.wrap(message, max(10, width - 6)), 4):
            self.safe_add(index, 3, line)
        self.safe_add(height - 1, 3, "Nhấn phím bất kỳ để tiếp tục", curses.color_pair(1))
        self.screen.refresh()
        self.screen.getch()

    @staticmethod
    def book_label(book: CatalogBook) -> str:
        authors = ", ".join(book.authors) or "Chưa rõ tác giả"
        categories = ", ".join(book.categories[:2])
        suffix = f" · {categories}" if categories else ""
        return f"{book.id} · {book.title} — {authors}{suffix}"

    def hline(self, row: int, column: int, length: int) -> None:
        self.safe_add(row, column, "─" * max(0, length), curses.color_pair(1))

    def safe_add(
        self, row: int, column: int, text: str, style: int = 0, max_width: int | None = None
    ) -> None:
        height, width = self.screen.getmaxyx()
        if row < 0 or row >= height or column < 0 or column >= width:
            return
        allowed = max(0, min(max_width or width, width - column - 1))
        try:
            self.screen.addnstr(row, column, text, allowed, style)
        except curses.error:
            pass


def run_tui(settings: Settings, registry) -> int:
    curses.wrapper(lambda screen: TuiApp(screen, settings, registry).run())
    return 0
