"""Bounded, same-origin fetcher for public website reference text."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections import deque
from html.parser import HTMLParser
from urllib.parse import unquote, urldefrag, urljoin, urlsplit, urlunsplit

import httpx


MAX_PAGES = 8
MAX_BYTES_PER_PAGE = 1_000_000
MAX_TOTAL_BYTES = 4_000_000
MAX_CONTEXT_CHARS = 24_000
REDIRECT_LIMIT = 3
DEFAULT_CLINIC_SOURCE_URL = "https://khamtieuhoa.bvtamtrisaigon.com.vn/"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "form", "button", "nav"}
SKIP_HINTS = (
    "symptom", "condition", "disease", "diagnos", "treatment", "medicine",
    "testimonial", "review", "booking", "question", "faq", "triage",
    "trieu-chung", "trieu chung", "benh-ly", "benh ly", "benh-hoc", "benh hoc",
    "chan-doan", "chan doan", "dieu-tri", "dieu tri", "thuoc", "hoi-dap", "hoi dap",
    "cau-hoi", "cau hoi", "cam-nhan", "cam nhan", "danh-gia", "danh gia",
    "dat-lich", "dat lich", "sang-loc", "sang loc",
)
SKIP_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js", ".zip"}


class WebsiteFetchError(ValueError):
    pass


def _canonical_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port
    except (ValueError, UnicodeError):
        raise WebsiteFetchError("Đường dẫn website chưa hợp lệ.") from None
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        raise WebsiteFetchError("Chỉ đồng bộ website công khai dùng HTTPS, không kèm thông tin đăng nhập.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise WebsiteFetchError("Hãy dùng tên miền HTTPS công khai thay vì địa chỉ IP.")
    path = parsed.path or "/"
    return urlunsplit(("https", host, path, "", "")), host


async def _assert_public_host(host: str) -> None:
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            443,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        raise WebsiteFetchError("Không tìm thấy website hoặc tên miền chưa hoạt động.") from None
    addresses = set()
    for record in records:
        try:
            addresses.add(ipaddress.ip_address(record[4][0].split("%", 1)[0]))
        except ValueError:
            continue
    if not addresses or any(not address.is_global for address in addresses):
        raise WebsiteFetchError("Website phải trỏ tới địa chỉ Internet công khai.")


class _PublicTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.skip_depth = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        hint = (values.get("id", "") + " " + values.get("class", "") + " " + values.get("aria-label", "")).lower()
        skip = self.skip_depth > 0 or tag.lower() in SKIP_TAGS or any(item in hint for item in SKIP_HINTS)
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "a" and not skip and values.get("href"):
            self.links.append(values["href"])
        if tag.lower() not in VOID_TAGS:
            self.stack.append((tag.lower(), skip))
            if skip:
                self.skip_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                removed = self.stack[index:]
                del self.stack[index:]
                self.skip_depth = max(0, self.skip_depth - sum(1 for _, skipped in removed if skipped))
                break

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if not self.skip_depth:
            clean = " ".join(data.split())
            if clean:
                self.parts.append(clean)


def _same_origin_link(href: str, base_url: str, host: str) -> str | None:
    absolute = urldefrag(urljoin(base_url, href.strip()))[0]
    try:
        parsed = urlsplit(absolute)
        link_host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port
    except (ValueError, UnicodeError):
        return None
    if parsed.scheme.lower() != "https" or link_host != host or port not in {None, 443}:
        return None
    if parsed.username or parsed.password or parsed.query:
        return None
    path = parsed.path or "/"
    normalized_path = unquote(path).casefold()
    if any(hint in normalized_path for hint in SKIP_HINTS):
        return None
    if any(path.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
        return None
    return urlunsplit(("https", host, path, "", ""))


async def _fetch_html(
    client: httpx.AsyncClient,
    start_url: str,
    host: str,
    max_bytes: int,
) -> tuple[str, str, int]:
    current = start_url
    for redirect_count in range(REDIRECT_LIMIT + 1):
        await _assert_public_host(host)
        try:
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count >= REDIRECT_LIMIT:
                        raise WebsiteFetchError("Website chuyển hướng quá nhiều lần.")
                    redirected = _same_origin_link(location, current, host)
                    if redirected is None:
                        raise WebsiteFetchError("Website chuyển hướng ra ngoài tên miền đã chọn.")
                    current = redirected
                    continue
                if response.status_code >= 400:
                    raise WebsiteFetchError("Website không cho phép đọc nội dung hoặc đang gặp lỗi.")
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise WebsiteFetchError("Địa chỉ này không trả về trang HTML công khai.")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > min(MAX_BYTES_PER_PAGE, max_bytes):
                        raise WebsiteFetchError("Website vượt quá giới hạn dung lượng đồng bộ.")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                return b"".join(chunks).decode(encoding, errors="replace"), current, size
        except WebsiteFetchError:
            raise
        except (httpx.HTTPError, OSError, UnicodeError):
            raise WebsiteFetchError("Không thể tải website lúc này. Hãy thử lại sau.") from None
    raise WebsiteFetchError("Website chuyển hướng quá nhiều lần.")


async def read_public_website(source_url: str) -> dict[str, object]:
    start_url, host = _canonical_url(source_url)
    await _assert_public_host(host)
    queue = deque([start_url])
    visited: set[str] = set()
    pages: list[str] = []
    total_bytes = 0
    timeout = httpx.Timeout(8.0, connect=4.0)
    headers = {"User-Agent": "HermesHub-WebsiteReader/1.0"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, headers=headers) as client:
        while queue and len(visited) < MAX_PAGES and total_bytes < MAX_TOTAL_BYTES:
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            html, final_url, page_size = await _fetch_html(client, url, host, MAX_TOTAL_BYTES - total_bytes)
            total_bytes += page_size
            parser = _PublicTextParser()
            parser.feed(html)
            title = " ".join(" ".join(parser.title_parts).split())
            if any(hint in title.casefold() for hint in SKIP_HINTS):
                continue
            text = " ".join(parser.parts).strip()
            if text:
                page = " | ".join(part for part in (title, final_url, text) if part)
                pages.append(page)
            for href in parser.links:
                next_url = _same_origin_link(href, final_url, host)
                if next_url and next_url not in visited and next_url not in queue:
                    queue.append(next_url)
            if len(pages) >= MAX_PAGES:
                break
    content = "\n\n".join(pages)[:MAX_CONTEXT_CHARS].strip()
    if len(content) < 120:
        raise WebsiteFetchError("Không tìm thấy đủ nội dung công khai để làm nguồn tham khảo.")
    return {"source_url": start_url, "content": content, "page_count": len(pages)}
