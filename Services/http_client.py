from __future__ import annotations

import json
import time
from http.cookiejar import Cookie, CookieJar
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from Config.settings import Settings
from Services.errors import CrawlerError


class HttpClient:
    def __init__(self, settings: Settings, access_token: str = "") -> None:
        self.settings = settings
        self.access_token = access_token
        self._last_request = 0.0
        self._cookie_jar = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))

    def set_cookie_header(self, value: str, domain: str) -> None:
        parsed = SimpleCookie()
        try:
            parsed.load(value)
        except CookieError as error:
            raise CrawlerError("Cookie phiên không hợp lệ.") from error
        if not parsed:
            raise CrawlerError("Cookie phiên không hợp lệ hoặc đang để trống.")
        for name, morsel in parsed.items():
            self._cookie_jar.set_cookie(Cookie(
                version=0, name=name, value=morsel.value,
                port=None, port_specified=False,
                domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
                path="/", path_specified=True, secure=True,
                expires=None, discard=True, comment=None, comment_url=None,
                rest={"HttpOnly": None}, rfc2109=False,
            ))

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        body, charset = self._request(url, headers=headers)
        return body.decode(charset, errors="replace")

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        try:
            return json.loads(self.get_text(url, headers=headers).lstrip("\ufeff"))
        except json.JSONDecodeError as error:
            raise CrawlerError(f"Dữ liệu JSON không hợp lệ từ {url}") from error

    def get_bytes(self, url: str) -> bytes:
        body, _ = self._request(url)
        return body

    def post_form_json(self, url: str, values: dict[str, str]) -> Any:
        body = urlencode(values).encode("utf-8")
        response, charset = self._request(
            url, method="POST", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            return json.loads(response.decode(charset, errors="replace").lstrip("\ufeff"))
        except json.JSONDecodeError as error:
            raise CrawlerError(f"Dữ liệu đăng nhập không hợp lệ từ {url}") from error

    def _request(
        self, url: str, method: str = "GET", data: bytes | None = None,
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
            with self._opener.open(request, timeout=self.settings.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read(), charset
        except (HTTPError, URLError, TimeoutError) as error:
            raise CrawlerError(f"Không thể tải {url}: {error}") from error
        finally:
            self._last_request = time.monotonic()

    @staticmethod
    def _ascii_url(url: str) -> str:
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
            return urlunsplit((
                parts.scheme, hostname,
                quote(parts.path, safe="/%:@!$&'()*+,;=-._~"),
                quote(parts.query, safe="=&?/:;+,%@!$'()*-._~"),
                quote(parts.fragment, safe=""),
            ))
        except (UnicodeError, ValueError) as error:
            raise CrawlerError(f"URL không hợp lệ: {url}") from error
