from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def use_project_venv() -> None:
    """Tự chạy lại bằng .venv khi Python hệ thống thiếu thư viện của dự án."""
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        return
    except ImportError:
        pass
    project_dir = Path(__file__).resolve().parent
    executable = (
        project_dir / ".venv" / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else project_dir / ".venv" / "bin" / "python"
    )
    if executable.is_file() and Path(sys.executable).resolve() != executable.resolve():
        os.execv(str(executable), [str(executable), str(Path(__file__).resolve()), *sys.argv[1:]])


def use_utf8_console() -> None:
    """Ép stdout/stderr dùng UTF-8 để không lỗi tiếng Việt trên console Windows."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


use_utf8_console()
use_project_venv()

from Config.settings import Settings
from Config.preferences import DownloadPreferences, remove_local_download
from Services.catalog import CatalogBook, CatalogStore, scan_and_save
from Services.errors import CrawlerError
from Services.google_drive import GoogleDriveService
from Services.output import OutputWriter
from Services.registry import build_registry


MENU = """
╭──────────────────────────────────────────╮
│              TRUYỆN CLI                  │
├──────────────────────────────────────────┤
│  1. Danh sách nguồn                      │
│  2. Quét và lưu catalog                  │
│  3. Xem / tìm trong catalog              │
│  4. Tải sách từ catalog                  │
│  5. Tìm trực tiếp trên website           │
│  0. Thoát                                │
╰──────────────────────────────────────────╯
"""


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="truyen",
        description="CLI thu thập dữ liệu truyện từ nhiều website.",
    )
    parser.add_argument("--output", type=Path, help="Thư mục lưu dữ liệu (mặc định: data).")

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sources", help="Liệt kê các nguồn truyện hiện có.")

    search = commands.add_parser("search", help="Tìm truyện trên một nguồn.")
    search.add_argument("source", help="Mã nguồn, ví dụ: truyenfull.")
    search.add_argument("keyword", help="Từ khóa cần tìm.")

    detail = commands.add_parser("detail", help="Lấy thông tin và danh sách chương.")
    detail.add_argument("source")
    detail.add_argument("url", help="URL trang truyện.")

    crawl = commands.add_parser("crawl", help="Tải truyện và lưu thành JSON.")
    crawl.add_argument("source")
    crawl.add_argument("url", help="URL trang truyện.")
    crawl.add_argument("--from-chapter", type=int, default=1, metavar="N")
    crawl.add_argument("--limit", type=int, help="Số chương tối đa cần tải.")

    scan = commands.add_parser("scan", help="Quét danh mục của nguồn và lưu JSON.")
    scan.add_argument("source")
    scan.add_argument("--limit", type=int, help="Chỉ quét N sách (mặc định: tất cả).")

    catalog = commands.add_parser("catalog", help="Tìm trong catalog JSON đã quét.")
    catalog.add_argument("source")
    catalog.add_argument("keyword", nargs="?", default="")
    catalog.add_argument("--limit", type=int, default=20)

    download = commands.add_parser("download", help="Chọn và tải ebook từ catalog.")
    download.add_argument("source")
    download.add_argument("book", nargs="?", help="ID hoặc từ khóa; bỏ trống để chọn.")
    download.add_argument("--format", dest="file_format", help="Ví dụ: epub, mobi, pdf.")
    download.add_argument("--from-chapter", type=int, default=1, metavar="N")
    download.add_argument("--limit", type=int, help="Số chương cần tải với nguồn truyện chữ.")
    return parser


def print_catalog(books: list[CatalogBook]) -> None:
    for book in books:
        authors = ", ".join(book.authors) or "Chưa rõ tác giả"
        print(f"{book.id:>6}  {book.title} — {authors}")


def upload_to_drive(
    settings: Settings,
    destination: Path,
    source: str,
    book_title: str = "",
    book_id: int | str | None = None,
) -> None:
    """Tải lên Drive nếu đã bật; không biến lỗi Drive thành lỗi tải local."""
    try:
        drive_url = GoogleDriveService(settings).upload_download(
            destination, source, book_title, book_id
        )
    except CrawlerError as error:
        print(f"Cảnh báo Google Drive: {error}", file=sys.stderr)
        return
    if drive_url:
        print(f"Đã tải lên Google Drive: {drive_url}")
        if not DownloadPreferences(settings).keep_local:
            removed = remove_local_download(destination)
            print(f"Đã xóa bản local: {removed}")


def choose_book(store: CatalogStore, value: str | None, crawler=None) -> CatalogBook:
    book_id = None
    if value and crawler and hasattr(crawler, "book_id_from_input"):
        book_id = crawler.book_id_from_input(value)
    elif value and value.isdigit():
        book_id = int(value)
    if book_id is not None:
        if crawler and hasattr(crawler, "get_catalog_book"):
            return crawler.get_catalog_book(book_id)
        for book in store.load():
            if book.id == book_id:
                return book
        raise CrawlerError(f"Không tìm thấy ID {book_id} trong catalog đã lưu.")
    matches = store.search(value or "", limit=20)
    if not matches:
        raise CrawlerError("Không tìm thấy sách trong catalog đã lưu.")
    if len(matches) == 1:
        return matches[0]
    print_catalog(matches)
    if not sys.stdin.isatty():
        raise CrawlerError("Có nhiều kết quả. Hãy truyền chính xác ID sách.")
    selected = input("Nhập ID sách muốn tải: ").strip()
    if not selected.isdigit():
        raise CrawlerError("ID sách không hợp lệ.")
    selected_id = int(selected)
    for book in matches:
        if book.id == selected_id:
            return book
    raise CrawlerError("ID vừa chọn không nằm trong danh sách hiển thị.")


def prompt_source(registry, capability: str | None = None):
    crawlers = [
        crawler
        for crawler in registry.all()
        if capability is None or hasattr(crawler, capability)
    ]
    print("\nNguồn hiện có:")
    for index, crawler in enumerate(crawlers, 1):
        print(f"  {index}. {crawler.name} ({crawler.key})")
    selected = input("Chọn nguồn: ").strip()
    if not selected.isdigit() or not 1 <= int(selected) <= len(crawlers):
        raise CrawlerError("Lựa chọn nguồn không hợp lệ.")
    return crawlers[int(selected) - 1]


def interactive_cli() -> int:
    settings = Settings.from_env()
    registry = build_registry(settings)
    while True:
        print(MENU)
        try:
            choice = input("Chọn chức năng: ").strip()
            if choice == "0":
                print("Hẹn gặp lại.")
                return 0
            if choice == "1":
                print()
                for crawler in registry.all():
                    print(f"  {crawler.key:<12} {crawler.name:<20} {crawler.base_url}")
            elif choice == "2":
                crawler = prompt_source(registry, "scan_catalog")
                raw_limit = input("Số sách cần quét (Enter = tất cả): ").strip()
                if raw_limit and not raw_limit.isdigit():
                    raise CrawlerError("Số lượng phải là số nguyên dương.")
                limit = int(raw_limit) if raw_limit else None
                saved, total, destination = scan_and_save(
                    crawler, CatalogStore(settings.output_dir, crawler.key), limit
                )
                print(f"Đã lưu {saved}/{total} sách vào {destination}")
            elif choice == "3":
                crawler = prompt_source(registry, "scan_catalog")
                keyword = input("Tên sách hoặc tác giả (Enter = xem tất cả): ").strip()
                books = CatalogStore(settings.output_dir, crawler.key).search(keyword, 50)
                print()
                print_catalog(books)
                print(f"\nHiển thị {len(books)} kết quả.")
            elif choice == "4":
                crawler = prompt_source(registry, "download")
                query = input("Nhập ID, tên sách hoặc Enter để xem danh sách: ").strip()
                selected = choose_book(
                    CatalogStore(settings.output_dir, crawler.key), query or None
                )
                detail = crawler.get_catalog_book(selected.id)
                if not detail.formats:
                    raise CrawlerError(f"'{detail.title}' hiện không có file để tải.")
                print(f"Định dạng có sẵn: {', '.join(detail.formats)}")
                file_format = input("Chọn định dạng: ").strip()
                if not file_format:
                    raise CrawlerError("Bạn chưa chọn định dạng.")
                destination = crawler.download(
                    detail.id,
                    file_format,
                    settings.output_dir / "downloads" / crawler.key,
                )
                print(f"Đã tải '{detail.title}' vào {destination}")
                upload_to_drive(
                    settings, destination, crawler.key, detail.title, detail.id
                )
            elif choice == "5":
                crawler = prompt_source(registry)
                keyword = input("Từ khóa tìm kiếm: ").strip()
                if not keyword:
                    raise CrawlerError("Từ khóa không được để trống.")
                books = crawler.search(keyword)
                if not books:
                    print("Không tìm thấy kết quả.")
                for index, book in enumerate(books, 1):
                    print(f"{index:>3}. {book.title}\n     {book.url}")
            else:
                print("Lựa chọn không hợp lệ.")
        except CrawlerError as error:
            print(f"\nLỗi: {error}", file=sys.stderr)
        except (EOFError, KeyboardInterrupt):
            print("\nĐã thoát.")
            return 0
        input("\nNhấn Enter để quay lại menu...")


def run(args: argparse.Namespace) -> int:
    settings = Settings.from_env(output_override=args.output)
    registry = build_registry(settings)

    if args.command == "sources":
        for crawler in registry.all():
            print(f"{crawler.key:<12} {crawler.name:<20} {crawler.base_url}")
        return 0

    crawler = registry.get(args.source)
    if args.command == "search":
        books = crawler.search(args.keyword)
        if not books:
            print("Không tìm thấy truyện phù hợp.")
        for index, book in enumerate(books, 1):
            print(f"{index:>3}. {book.title}\n     {book.url}")
        return 0

    if args.command == "scan":
        if not hasattr(crawler, "scan_catalog"):
            raise CrawlerError(f"Nguồn '{crawler.key}' không hỗ trợ quét catalog.")
        saved, total, destination = scan_and_save(
            crawler, CatalogStore(settings.output_dir, crawler.key), args.limit
        )
        print(f"Đã lưu {saved}/{total} sách vào {destination}")
        return 0

    if args.command == "catalog":
        if args.limit < 1:
            raise CrawlerError("--limit phải lớn hơn hoặc bằng 1.")
        books = CatalogStore(settings.output_dir, crawler.key).search(
            args.keyword, args.limit
        )
        print_catalog(books)
        print(f"Hiển thị {len(books)} kết quả.")
        return 0

    if args.command == "download":
        if not hasattr(crawler, "download") or not hasattr(crawler, "get_catalog_book"):
            raise CrawlerError(f"Nguồn '{crawler.key}' không hỗ trợ tải ebook.")
        store = CatalogStore(settings.output_dir, crawler.key)
        selected = choose_book(store, args.book, crawler)
        detail = crawler.get_catalog_book(selected.id)
        if not detail.formats:
            raise CrawlerError(f"'{detail.title}' hiện không có file để tải.")
        file_format = args.file_format
        if not file_format:
            if not sys.stdin.isatty():
                raise CrawlerError(
                    f"Hãy chọn --format. Có sẵn: {', '.join(detail.formats)}."
                )
            print(f"Định dạng có sẵn: {', '.join(detail.formats)}")
            file_format = input("Chọn định dạng: ").strip()
        download_args = {}
        if getattr(crawler, "download_mode", "") == "chapters":
            download_args = {"start": args.from_chapter, "limit": args.limit}
        destination = crawler.download(
            detail.id,
            file_format,
            settings.output_dir / "downloads" / crawler.key,
            **download_args,
        )
        print(f"Đã tải '{detail.title}' vào {destination}")
        upload_to_drive(settings, destination, crawler.key, detail.title, detail.id)
        return 0

    if args.command == "detail":
        book = crawler.get_book(args.url)
        print(f"Tên: {book.title}")
        print(f"Tác giả: {book.author or 'Chưa rõ'}")
        print(f"Số chương: {len(book.chapters)}")
        print(f"URL: {book.url}")
        return 0

    if args.command != "crawl":
        raise CrawlerError(f"Lệnh không được hỗ trợ: {args.command}")
    if args.from_chapter < 1:
        raise CrawlerError("--from-chapter phải lớn hơn hoặc bằng 1.")
    book = crawler.crawl(
        args.url,
        start=args.from_chapter,
        limit=args.limit,
    )
    destination = OutputWriter(settings.output_dir).write(book, crawler.key)
    print(f"Đã lưu {len(book.chapters)} chương vào {destination}")
    upload_to_drive(settings, destination, crawler.key, book.title)
    return 0


def main() -> int:
    try:
        if len(sys.argv) == 1:
            # curses/windows-curses is unreliable in frozen Windows programs,
            # especially when the executable is run through Wine.
            if sys.platform != "win32" and sys.stdin.isatty() and sys.stdout.isatty():
                try:
                    from Services.tui import run_tui
                except ImportError as error:
                    # ``curses`` is not bundled with CPython on Windows. Keep the
                    # executable usable if windows-curses/_curses was omitted by
                    # the build environment, while surfacing unrelated imports.
                    if getattr(error, "name", None) not in {"curses", "_curses"}:
                        raise
                    print(
                        "Cảnh báo: không tải được giao diện TUI; chuyển sang menu thường.",
                        file=sys.stderr,
                    )
                    return interactive_cli()

                settings = Settings.from_env()
                return run_tui(settings, build_registry(settings))
            return interactive_cli()
        return run(create_parser().parse_args())
    except (CrawlerError, ValueError) as error:
        print(f"Lỗi: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nĐã dừng.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
