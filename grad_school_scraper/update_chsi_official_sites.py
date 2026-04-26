#!/usr/bin/env python3
"""Update school official-site columns in chsi_seeds_national.csv.

The CHSI detail pages usually do not expose a dedicated "official site" field.
Their contact pages do contain school-run external links, so this script extracts
those links and selects the most plausible primary site for each school.
"""

from __future__ import annotations

import csv
import html
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlparse, urlunparse


INPUT = Path(__file__).with_name("chsi_seeds_national.csv")
CHSI_BASE = "https://yz.chsi.com.cn"
MAX_WORKERS = 8
FETCH_TIMEOUT = "25"
MAX_EXTRA_CATEGORY_PAGES = 8
MAX_BROCHURE_PAGES = 3

BLOCKED_HOST_PARTS = (
    "chsi.com.cn",
    "chei.com.cn",
    "google-analytics.com",
    "googletagmanager.com",
    "baidu.com",
    "bing.com",
    "miit.gov.cn",
    "beian.gov.cn",
)

BAD_URL_PARTS = (
    "javascript:",
    "mailto:",
    "tel:",
    "weixin",
    "wechat",
    "qq.com",
    "weibo.com",
    "map.baidu",
    "amap.com",
)

PRIMARY_TEXT_HINTS = (
    "研究生院",
    "研究生招生",
    "研招",
    "招生",
    "官网",
    "主页",
    "网址",
    "网站",
    "联系方式",
    "联系",
)


@dataclass
class Link:
    url: str
    text: str


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.links: list[Link] = []
        self._in_title = False
        self._href_stack: list[str] = []
        self._text_stack: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "a":
            attrs_dict = {k.lower(): v for k, v in attrs if v is not None}
            self._href_stack.append(attrs_dict.get("href", ""))
            self._text_stack.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        if tag.lower() == "a" and self._href_stack:
            href = self._href_stack.pop()
            text = clean_text("".join(self._text_stack.pop()))
            if href:
                self.links.append(Link(href, text))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._text_stack:
            self._text_stack[-1].append(data)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def fetch(url: str) -> tuple[int | None, str, str | None]:
    if not url:
        return None, "", "missing url"
    try:
        proc = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                FETCH_TIMEOUT,
                "-A",
                "Mozilla/5.0",
                "-sS",
                "-w",
                "\n__HTTP_STATUS__:%{http_code}",
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return None, "", str(exc)
    output = proc.stdout
    marker = "\n__HTTP_STATUS__:"
    if marker in output:
        body, status_text = output.rsplit(marker, 1)
        try:
            status = int(status_text.strip()[:3])
        except ValueError:
            status = None
    else:
        body = output
        status = None
    error = clean_text(proc.stderr) if proc.returncode else None
    if proc.returncode and not error:
        error = f"curl exit {proc.returncode}"
    return status, body, error


def normalize_url(raw_url: str, base_url: str) -> str | None:
    raw_url = html.unescape(clean_text(raw_url)).strip(" \t\r\n\"'<>（）()[]【】,，。；;、")
    if not raw_url:
        return None
    lower = raw_url.lower()
    if any(part in lower for part in BAD_URL_PARTS):
        return None
    if raw_url.startswith("//"):
        raw_url = "https:" + raw_url
    try:
        url = urljoin(base_url, raw_url)
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    if any(part in host for part in BLOCKED_HOST_PARTS):
        return None
    path = quote(unquote(parsed.path), safe="/:%@+~#=,.()-_")
    query = "&".join(
        f"{quote(k, safe=':/?@+~#=,.()-_')}={quote(v, safe=':/?@+~#=,.()-_')}"
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
    )
    return urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))


def extract_external_links(body: str, base_url: str) -> tuple[str, list[Link]]:
    parser = LinkParser()
    parser.feed(body)
    title = clean_text("".join(parser.title_parts))
    seen: set[str] = set()
    links: list[Link] = []
    for link in parser.links:
        url = normalize_url(link.url, base_url)
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(Link(url, link.text))
    text_body = re.sub(r"<[^>]+>", " ", body)
    for match in re.finditer(r"https?://[^\s\"'<>，。；;、)）]+|www\.[^\s\"'<>，。；;、)）]+", text_body):
        raw_url = match.group(0)
        if raw_url.startswith("www."):
            raw_url = "https://" + raw_url
        url = normalize_url(raw_url, base_url)
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(Link(url, ""))
    return title, links


def extract_category_urls(body: str, base_url: str, sch_id: str) -> list[str]:
    parser = LinkParser()
    parser.feed(body)
    urls: list[str] = []
    seen: set[str] = set()
    pattern = f"/sch/schoolInfo--schId-{sch_id},categoryId-"
    for link in parser.links:
        if pattern not in link.url:
            continue
        url = urljoin(base_url, link.url)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def extract_chsi_content_urls(body: str, base_url: str) -> list[str]:
    parser = LinkParser()
    parser.feed(body)
    urls: list[str] = []
    seen: set[str] = set()
    for link in parser.links:
        if "/sch/viewZszc--" not in link.url and "/sch/viewBulletin--" not in link.url:
            continue
        url = urljoin(base_url, link.url)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def root_candidate(url: str) -> str | None:
    parsed = urlparse(url)
    labels = parsed.netloc.lower().split(".")
    if len(labels) < 3:
        return None
    if labels[-2:] != ["edu", "cn"]:
        return None
    registrable = ".".join(labels[-3:])
    if parsed.netloc.lower() == registrable or labels[0] == "www":
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return f"https://www.{registrable}"


def score_link(link: Link, school_name: str) -> int:
    url = link.url.lower()
    host = urlparse(link.url).netloc.lower()
    text = link.text
    score = 0
    if school_name and school_name in text:
        score += 12
    if host.endswith(".edu.cn") or host.endswith(".edu"):
        score += 10
    if any(hint in text for hint in PRIMARY_TEXT_HINTS):
        score += 8
    if any(hint in url for hint in ("yjs", "yz", "zs", "graduate", "admission")):
        score += 5
    if url.startswith("https://"):
        score += 2
    if re.search(r"/(info|content|article|news|tzgg|gg|detail)[/-]", url):
        score -= 6
    if any(part in host for part in ("mp.weixin", "share", "job", "email")):
        score -= 20
    return score


def choose_primary(links: Iterable[Link], school_name: str) -> str:
    links = list(links)
    if not links:
        return ""
    best = max(links, key=lambda link: score_link(link, school_name))
    root = root_candidate(best.url)
    if root:
        return root
    return best.url


def process_row(index: int, row: dict[str, str]) -> tuple[int, dict[str, str]]:
    page_url = row.get("yz_contact_url") or row.get("yz_detail_url") or ""
    status, body, error = fetch(page_url)
    candidates: list[Link] = []
    title = ""
    if body:
        title, candidates = extract_external_links(body, page_url)
    if not candidates and row.get("yz_detail_url"):
        detail_status, detail_body, detail_error = fetch(row["yz_detail_url"])
        if not error and detail_error:
            error = detail_error
        if detail_body:
            for category_url in extract_category_urls(
                detail_body, row["yz_detail_url"], row.get("school_id", "")
            )[:MAX_EXTRA_CATEGORY_PAGES]:
                _, page_body, page_error = fetch(category_url)
                if not error and page_error:
                    error = page_error
                if not page_body:
                    continue
                _, extra_links = extract_external_links(page_body, category_url)
                existing = {link.url for link in candidates}
                candidates.extend(link for link in extra_links if link.url not in existing)
                if candidates:
                    break
        if status is None and detail_status is not None:
            status = detail_status
    if not candidates and row.get("yz_brochure_url"):
        _, brochure_body, brochure_error = fetch(row["yz_brochure_url"])
        if not error and brochure_error:
            error = brochure_error
        if brochure_body:
            for content_url in extract_chsi_content_urls(
                brochure_body, row["yz_brochure_url"]
            )[:MAX_BROCHURE_PAGES]:
                _, content_body, content_error = fetch(content_url)
                if not error and content_error:
                    error = content_error
                if not content_body:
                    continue
                _, extra_links = extract_external_links(content_body, content_url)
                existing = {link.url for link in candidates}
                candidates.extend(link for link in extra_links if link.url not in existing)
                if candidates:
                    break
    primary = choose_primary(candidates, row.get("school_name", ""))
    now = datetime.now(timezone.utc).isoformat()
    updated = dict(row)
    if status is not None:
        updated["contact_page_status"] = str(status)
    if title:
        updated["contact_page_title"] = title
    if error:
        updated["contact_error"] = error
    else:
        updated["contact_error"] = ""
    if primary:
        updated["official_site_url"] = primary
    updated["external_site_candidates"] = " | ".join(link.url for link in candidates)
    updated["fetched_at"] = now
    return index, updated


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else INPUT
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    started = time.time()
    updated_rows: list[dict[str, str] | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_row, i, row) for i, row in enumerate(rows)]
        for done, future in enumerate(as_completed(futures), 1):
            index, updated = future.result()
            updated_rows[index] = updated
            if done % 50 == 0 or done == len(rows):
                filled = sum(1 for row in updated_rows if row and row.get("official_site_url"))
                print(f"processed {done}/{len(rows)}; official_site_url filled {filled}")

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_bytes(path.read_bytes())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(row for row in updated_rows if row is not None)
    elapsed = time.time() - started
    filled = sum(1 for row in updated_rows if row and row.get("official_site_url"))
    print(f"wrote {path}; backup {backup}; filled {filled}/{len(rows)} in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
