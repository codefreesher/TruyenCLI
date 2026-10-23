class CrawlerError(Exception):
    """Lỗi có thể hiển thị trực tiếp trên CLI."""


class SourceNotFoundError(CrawlerError):
    pass


class ParseError(CrawlerError):
    pass
