#!/usr/bin/env python3
"""Add official undergraduate-program and faculty-team page links.

Inputs are the school/institution homepages in ``official_site_url``. The script
only accepts links under the same official school domain and filters admissions,
graduate-school, recruiting, social-media, and external-platform pages.
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
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlparse, urlunparse


INPUT = Path(__file__).with_name("chsi_seeds_national.csv")
MAX_WORKERS = 14
FETCH_TIMEOUT = "8"
MAX_NAV_PAGES = 4

UNDERGRAD_FIELD = "undergraduate_programs_url"
FACULTY_FIELD = "faculty_team_url"

BLOCKED_HOST_PARTS = (
    "chsi.com.cn",
    "chsi.cn",
    "chei.com.cn",
    "baidu.com",
    "bing.com",
    "qq.com",
    "weibo.com",
    "weixin",
    "wechat",
    "douban.com",
    "cnki.net",
    "wanfangdata.com.cn",
)

BAD_URL_PARTS = (
    "javascript:",
    "mailto:",
    "tel:",
    "#",
    "download",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".rar",
)

ADMISSIONS_HOST_LABELS = {
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
    "bkzs",
    "zsb",
    "lqcx",
    "admission",
    "admissions",
    "job",
    "jobs",
    "rczp",
    "zhaopin",
    "zp",
}

NEGATIVE_TEXT = (
    "招生",
    "研究生",
    "研招",
    "硕士",
    "博士",
    "招聘",
    "招考",
    "录取",
    "就业",
    "继续教育",
    "留学生",
    "国际学生",
)

UNDERGRAD_POSITIVE = (
    "本科专业",
    "专业设置",
    "本科教育",
    "本科生教育",
    "本科教学",
    "专业介绍",
    "本科专业目录",
    "院系专业",
    "教育教学",
    "人才培养",
)

UNDERGRAD_URL_POSITIVE = (
    "benke",
    "bks",
    "bkzy",
    "bkzn",
    "undergraduate",
    "major",
    "majors",
    "program",
    "profession",
    "zysz",
    "rcpy",
    "jypy",
    "jwc",
    "jiaowu",
    "academic",
)

FACULTY_POSITIVE = (
    "师资队伍",
    "师资概况",
    "师资力量",
    "教师队伍",
    "人才队伍",
    "专任教师",
    "师资",
    "教学名师",
)

FACULTY_URL_POSITIVE = (
    "szdw",
    "szll",
    "szgk",
    "faculty",
    "teacher",
    "teachers",
    "staff",
    "rcdw",
    "rencai",
    "talent",
)

NAV_TEXT_HINTS = (
    "学校概况",
    "学校简介",
    "院系设置",
    "机构设置",
    "人才培养",
    "教育教学",
    "本科教育",
    "本科生教育",
    "师资队伍",
    "师资力量",
    "教师队伍",
)


@dataclass
class Link:
    url: str
    text: str
    source_url: str


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self._href_stack: list[str] = []
        self._text_stack: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {k.lower(): v for k, v in attrs if v is not None}
        self._href_stack.append(attrs_dict.get("href", ""))
        self._text_stack.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href_stack:
            href = self._href_stack.pop()
            text = clean_text("".join(self._text_stack.pop()))
            if href:
                self.links.append(Link(href, text, ""))

    def handle_data(self, data: str) -> None:
        if self._text_stack:
            self._text_stack[-1].append(data)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def fetch(url: str) -> str:
    proc = subprocess.run(
        [
            "curl",
            "-L",
            "--max-time",
            FETCH_TIMEOUT,
            "-A",
            "Mozilla/5.0",
            "-sS",
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode:
        return ""
    return proc.stdout


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


def registrable_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2:] == ["edu", "cn"]:
        return ".".join(labels[-3:])
    if len(labels) >= 4 and labels[-2] in {"ac", "com", "net", "org", "gov"} and labels[-1] == "cn":
        return ".".join(labels[-3:])
    if len(labels) >= 3 and labels[-1] == "cn":
        return ".".join(labels[-2:])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


def same_school_domain(url: str, official_url: str) -> bool:
    host = urlparse(url).netloc.lower()
    root = registrable_domain(official_url)
    return host == root or host == "www." + root or host.endswith("." + root)


def is_bad_school_page(link: Link) -> bool:
    parsed = urlparse(link.url)
    labels = parsed.netloc.lower().split(".")
    path = parsed.path.lower()
    text = link.text
    if any(label in ADMISSIONS_HOST_LABELS for label in labels):
        return True
    if any(part in path for part in ("/yjs", "/yjsc", "/yjsy", "/graduate", "/grad", "/yz", "/admission", "/job", "/rczp", "/zhaopin")):
        return True
    return False


def extract_links(body: str, base_url: str, official_url: str) -> list[Link]:
    parser = LinkParser()
    try:
        parser.feed(body)
    except Exception:
        return []
    links: list[Link] = []
    seen: set[str] = set()
    for link in parser.links:
        url = normalize_url(link.url, base_url)
        if not url or url in seen:
            continue
        if not same_school_domain(url, official_url):
            continue
        normalized = Link(url, link.text, base_url)
        if is_bad_school_page(normalized):
            continue
        seen.add(url)
        links.append(normalized)
    return links


def score_link(link: Link, positive_text: tuple[str, ...], positive_url: tuple[str, ...]) -> int:
    url = link.url.lower()
    text = link.text
    score = 0
    for idx, hint in enumerate(positive_text):
        if hint in text:
            score += 120 - idx
    for idx, hint in enumerate(positive_url):
        if hint in url:
            score += 45 - idx
    if any(word in text for word in NEGATIVE_TEXT):
        score -= 100
    if re.search(r"/(info|content|article|news|tzgg|gg|detail|zx|xw)[/-]", url):
        score -= 100
    if url.endswith(("/", "index.htm", "index.html", "index.shtml")):
        score += 8
    if len(url) > 120:
        score -= 10
    return score


def nav_score(link: Link) -> int:
    text = link.text
    url = link.url.lower()
    score = 0
    for idx, hint in enumerate(NAV_TEXT_HINTS):
        if hint in text:
            score += 100 - idx
    if any(hint in url for hint in ("about", "intro", "profile", "rcpy", "jypy", "szdw", "faculty", "teacher")):
        score += 20
    if any(word in text for word in NEGATIVE_TEXT):
        score -= 100
    return score


def choose_best(links: list[Link], positive_text: tuple[str, ...], positive_url: tuple[str, ...]) -> str:
    scored = [(score_link(link, positive_text, positive_url), link.url) for link in links]
    scored = [(score, url) for score, url in scored if score >= 40]
    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return scored[0][1]


def process_row(index: int, row: dict[str, str]) -> tuple[int, dict[str, str]]:
    official_url = row.get("official_site_url", "").strip()
    updated = dict(row)
    if not official_url:
        updated.setdefault(UNDERGRAD_FIELD, "")
        updated.setdefault(FACULTY_FIELD, "")
        return index, updated

    body = fetch(official_url)
    all_links = extract_links(body, official_url, official_url) if body else []
    nav_links = sorted(all_links, key=nav_score, reverse=True)
    for link in [link for link in nav_links if nav_score(link) > 0][:MAX_NAV_PAGES]:
        sub_body = fetch(link.url)
        if not sub_body:
            continue
        existing = {candidate.url for candidate in all_links}
        for candidate in extract_links(sub_body, link.url, official_url):
            if candidate.url not in existing:
                all_links.append(candidate)
                existing.add(candidate.url)

    updated[UNDERGRAD_FIELD] = choose_best(all_links, UNDERGRAD_POSITIVE, UNDERGRAD_URL_POSITIVE)
    updated[FACULTY_FIELD] = choose_best(all_links, FACULTY_POSITIVE, FACULTY_URL_POSITIVE)
    return index, updated


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else INPUT
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    for field in (UNDERGRAD_FIELD, FACULTY_FIELD):
        if field not in fieldnames:
            fieldnames.append(field)

    started = time.time()
    updated_rows: list[dict[str, str] | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_row, i, row) for i, row in enumerate(rows)]
        for done, future in enumerate(as_completed(futures), 1):
            index, updated = future.result()
            updated_rows[index] = updated
            if done % 50 == 0 or done == len(rows):
                undergrad = sum(1 for row in updated_rows if row and row.get(UNDERGRAD_FIELD))
                faculty = sum(1 for row in updated_rows if row and row.get(FACULTY_FIELD))
                print(
                    f"processed {done}/{len(rows)}; "
                    f"undergraduate {undergrad}; faculty {faculty}",
                    flush=True,
                )

    backup = path.with_suffix(path.suffix + ".official-pages.bak")
    backup.write_bytes(path.read_bytes())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(row for row in updated_rows if row is not None)

    elapsed = time.time() - started
    undergrad = sum(1 for row in updated_rows if row and row.get(UNDERGRAD_FIELD))
    faculty = sum(1 for row in updated_rows if row and row.get(FACULTY_FIELD))
    print(f"wrote {path}; undergraduate {undergrad}; faculty {faculty}; elapsed {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
