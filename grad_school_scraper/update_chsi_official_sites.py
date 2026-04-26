#!/usr/bin/env python3
"""Update school official-site columns in chsi_seeds_national.csv.

This script treats ``official_site_url`` as the school/institution's main
homepage, not as a graduate-school or admissions homepage. Auxiliary links in
``external_site_candidates`` are school-run pages, especially profile and
department/college pages found on CHSI pages.
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
FETCH_TIMEOUT = "12"
TARGET_CATEGORY_NAMES = ("院系设置", "院校简介", "科研条件", "其它")
MAX_EXTRA_CATEGORY_PAGES = 4

BLOCKED_HOST_PARTS = (
    "chsi.com.cn",
    "chsi.cn",
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
    "douban.com",
    "cnki.net",
    "wanfangdata.com.cn",
    "map.baidu",
    "amap.com",
)

SCHOOL_PAGE_TEXT_HINTS = (
    "学校",
    "大学",
    "学院",
    "院校",
    "院系",
    "学部",
    "系所",
    "机构",
    "简介",
    "概况",
    "官网",
    "主页",
    "网址",
    "网站",
)

ADMISSIONS_HOST_LABELS = (
    "yjs",
    "yjsc",
    "yjsy",
    "yjsxy",
    "graduate",
    "grad",
    "yz",
    "yzb",
    "yanzhao",
    "zhaosheng",
    "zs",
    "zsxx",
    "yjszs",
    "admission",
    "admissions",
    "graduateschool",
    "pgs",
    "grs",
    "gs",
)

ADMISSIONS_PATH_PARTS = (
    "/yjs",
    "/yjsc",
    "/yjsy",
    "/graduate",
    "/grad",
    "/yanzhao",
    "/zhaosheng",
    "/admission",
    "/admissions",
    "/pgs",
    "/grs",
    "/zsxx",
)

ADMISSIONS_TEXT_HINTS = (
    "研究生",
    "研招",
    "招生",
    "录取",
    "硕士",
    "博士",
    "联系办法",
    "联系方式",
)


@dataclass
class Link:
    url: str
    text: str
    source: str = ""


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
    if parsed.username or parsed.password:
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


def extract_category_urls(body: str, base_url: str, sch_id: str) -> list[Link]:
    parser = LinkParser()
    parser.feed(body)
    urls: list[Link] = []
    seen: set[str] = set()
    pattern = f"/sch/schoolInfo--schId-{sch_id},categoryId-"
    for link in parser.links:
        if pattern not in link.url:
            continue
        url = urljoin(base_url, link.url)
        if url in seen:
            continue
        seen.add(url)
        urls.append(Link(url, link.text))
    return urls


def institution_homepage_candidate(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not host:
        return None
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2:] == ["edu", "cn"]:
        registrable = ".".join(labels[-3:])
        if host == registrable:
            return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        return f"https://www.{registrable}"
    if len(labels) >= 4 and labels[-2] in {"ac", "com", "net", "org", "gov"} and labels[-1] == "cn":
        registrable = ".".join(labels[-3:])
        if host == registrable or host == "www." + registrable:
            return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        return f"https://www.{registrable}"
    if len(labels) >= 3 and labels[-1] == "cn":
        registrable = ".".join(labels[-2:])
        if host == registrable or host == "www." + registrable:
            return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        return f"https://www.{registrable}"
    if len(labels) >= 2:
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return None


def is_admissions_link(link: Link) -> bool:
    parsed = urlparse(link.url)
    host_labels = parsed.netloc.lower().split(".")
    path = parsed.path.lower()
    text = link.text
    return any(label in ADMISSIONS_HOST_LABELS for label in host_labels) or any(
        part in path for part in ADMISSIONS_PATH_PARTS
    ) or any(
        hint in text for hint in ADMISSIONS_TEXT_HINTS
    )


def is_school_page_link(link: Link) -> bool:
    if is_admissions_link(link):
        return False
    if link.source == "existing_official":
        return True
    url = link.url.lower()
    host = urlparse(link.url).netloc.lower()
    text = link.text
    if host.endswith(".edu.cn") or host.endswith(".edu"):
        return True
    if any(hint in text for hint in SCHOOL_PAGE_TEXT_HINTS):
        return True
    return any(hint in url for hint in ("about", "intro", "profile", "department", "college"))


def score_homepage(homepage: str, source_links: list[Link], school_name: str) -> int:
    parsed = urlparse(homepage)
    host = parsed.netloc.lower()
    score = 0
    if host.startswith("www."):
        score += 8
    if host.endswith(".edu.cn") or host.endswith(".edu"):
        score += 25
    if homepage.startswith("https://"):
        score += 4
    if any(link.source in {"院校简介", "院系设置"} for link in source_links):
        score += 12
    if any(school_name and school_name in link.text for link in source_links):
        score += 10
    if any(any(hint in link.text for hint in SCHOOL_PAGE_TEXT_HINTS) for link in source_links):
        score += 8
    if any(is_admissions_link(link) for link in source_links):
        score -= 10
    return score


def choose_primary(links: Iterable[Link], school_name: str) -> str:
    grouped: dict[str, list[Link]] = {}
    for link in links:
        homepage = institution_homepage_candidate(link.url)
        if not homepage:
            continue
        if not is_school_page_link(link):
            continue
        grouped.setdefault(homepage, []).append(link)
    if not grouped:
        return ""
    return max(grouped, key=lambda homepage: score_homepage(homepage, grouped[homepage], school_name))


def sort_candidate_links(links: Iterable[Link], primary: str) -> list[str]:
    seen: set[str] = set()
    candidates: list[tuple[str, Link]] = []
    primary_host = urlparse(primary).netloc.lower()
    for link in links:
        candidate_url = (
            institution_homepage_candidate(link.url)
            if link.source in {"existing_official", "existing_candidate"}
            else link.url
        )
        if not candidate_url or candidate_url in seen:
            continue
        if not is_school_page_link(link):
            continue
        host = urlparse(link.url).netloc.lower()
        if primary_host and host != primary_host and not host.endswith("." + primary_host.removeprefix("www.")):
            root = institution_homepage_candidate(link.url)
            if root != primary:
                continue
        seen.add(candidate_url)
        candidates.append((candidate_url, link))
    candidates.sort(
        key=lambda item: (
            0 if item[0] == primary else 1,
            0 if item[1].source == "院系设置" else 1,
            0 if any(hint in item[1].text for hint in ("院系", "学院", "学部", "机构")) else 1,
            item[0],
        )
    )
    return [url for url, _ in candidates]


def process_row(index: int, row: dict[str, str]) -> tuple[int, dict[str, str]]:
    page_url = row.get("yz_contact_url") or row.get("yz_detail_url") or ""
    status, body, error = fetch(page_url)
    candidates: list[Link] = []
    title = ""
    if body:
        title, contact_links = extract_external_links(body, page_url)
        for link in contact_links:
            link.source = "联系办法"
        candidates.extend(contact_links)
    if row.get("yz_detail_url"):
        detail_status, detail_body, detail_error = fetch(row["yz_detail_url"])
        if not error and detail_error:
            error = detail_error
        if detail_body:
            category_links = extract_category_urls(
                detail_body, row["yz_detail_url"], row.get("school_id", "")
            )
            category_links = [
                link for link in category_links if link.text in TARGET_CATEGORY_NAMES
            ] or category_links[:MAX_EXTRA_CATEGORY_PAGES]
            category_links.sort(
                key=lambda link: (
                    0 if link.text == "院系设置" else 1,
                    0 if link.text == "院校简介" else 1,
                    0 if link.text in {"科研条件", "其它"} else 1,
                    link.text,
                )
            )
            for category in category_links[:MAX_EXTRA_CATEGORY_PAGES]:
                _, page_body, page_error = fetch(category.url)
                if not error and page_error:
                    error = page_error
                if not page_body:
                    continue
                _, extra_links = extract_external_links(page_body, category.url)
                existing = {link.url for link in candidates}
                for link in extra_links:
                    link.source = category.text
                    if link.url not in existing:
                        candidates.append(link)
                        existing.add(link.url)
        if status is None and detail_status is not None:
            status = detail_status
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
    updated["official_site_url"] = primary
    updated["external_site_candidates"] = " | ".join(sort_candidate_links(candidates, primary))
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
