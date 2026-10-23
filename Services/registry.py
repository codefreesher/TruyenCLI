from __future__ import annotations

from Config.settings import Settings
from KhoSach.crawler import KhoSachCrawler
from MeoCamMap.crawler import MeoCamMapCrawler
from Services.base_crawler import BaseCrawler
from Services.errors import SourceNotFoundError
from TruyenFull.crawler import TruyenFullCrawler


class CrawlerRegistry:
    def __init__(self) -> None:
        self._crawlers: dict[str, BaseCrawler] = {}

    def register(self, crawler: BaseCrawler) -> None:
        if crawler.key in self._crawlers:
            raise ValueError(f"Nguồn '{crawler.key}' đã được đăng ký.")
        self._crawlers[crawler.key] = crawler

    def get(self, key: str) -> BaseCrawler:
        try:
            return self._crawlers[key.lower()]
        except KeyError as error:
            available = ", ".join(sorted(self._crawlers))
            raise SourceNotFoundError(
                f"Không có nguồn '{key}'. Nguồn hiện có: {available}"
            ) from error

    def all(self) -> list[BaseCrawler]:
        return sorted(self._crawlers.values(), key=lambda crawler: crawler.key)


def build_registry(settings: Settings) -> CrawlerRegistry:
    registry = CrawlerRegistry()
    registry.register(KhoSachCrawler(settings))
    registry.register(MeoCamMapCrawler(settings))
    registry.register(TruyenFullCrawler(settings))
    return registry
