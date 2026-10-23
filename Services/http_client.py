from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from Config.settings import Settings
from Services.errors import CrawlerError


class HttpClient:
    def __init__(self, settings: Settings, access_token: str = "") -> None:
        self.settings = settings
        self.access_token = access_token
        self._last_request = 0.0

    def get_text(self, url: str) -> str:
        body, charset = self._request(url)
        return body.decode(charset, errors="replace")

    def get_json(self, url: str) -> Any:
        try:
            return json.loads(self.get_text(url))
        except json.JSONDecodeError as error:
            raise CrawlerError(f"Dữ liệu JSON không hợp lệ từ {url}") from error

    def get_bytes(self, url: str) -> bytes:
        body, _ = self._request(url)
        return body

    def post_form_json(self, url: str, values: dict[str, str]) -> Any:
        body = urlencode(values).encode("utf-8")
        response, charset = self._request(
            url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            return json.loads(response.decode(charset, errors="replace"))
        except json.JSONDecodeError as error:
            raise CrawlerError(f"Dữ liệu đăng nhập không hợp lệ từ {url}") from error

    def _request(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[bytes, str]:
        url = self._ascii_url(url)
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.settings.request_delay:
            time.sleep(self.settings.request_delay - elapsed)
        request_headers = {"User-Agent": self.settings.user_agent, **(headers or {})}
        if self.access_token:
            request_headers["Authorization"] = f"Bearer {self.access_token}"
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=self.settings.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read(), charset
        except (HTTPError, URLError, TimeoutError) as error:
            raise CrawlerError(f"Không thể tải {url}: {error}") from error
        finally:
            self._last_request = time.monotonic()

    @staticmethod
    def _ascii_url(url: str) -> str:
        """Mã hóa URL Unicode theo RFC 3986 trước khi đưa cho urllib."""
        try:
            parts = urlsplit(url)
            hostname = parts.hostname.encode("idna").decode("ascii") if parts.hostname else ""
            if parts.port:
                hostname = f"{hostname}:{parts.port}"
            if parts.username:
                userinfo = quote(parts.username, safe="")
                if parts.password:
                    userinfo += f":{quote(parts.password, safe='')}"
                hostname = f"{userinfo}@{hostname}"
            return urlunsplit(
                (
                    parts.scheme,
                    hostname,
                    quote(parts.path, safe="/%:@!$&'()*+,;=-._~"),
                    quote(parts.query, safe="=&?/:;+,%@!$'()*-._~"),
                    quote(parts.fragment, safe=""),
                )
            )
        except (UnicodeError, ValueError) as error:
            raise CrawlerError(f"URL không hợp lệ: {url}") from error
