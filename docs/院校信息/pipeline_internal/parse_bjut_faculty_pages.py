#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse Beijing University of Technology faculty list pages.

This is a school-specific parser for BJUT department faculty pages. Many BJUT
faculty pages expose teacher names as plain anchors under list/category pages,
while the generic profile extractor can mistake category labels for people.
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) bjut-faculty-parser/0.1"
NAME_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,6}$")
TITLE_RE = re.compile(r"(院士|教授|副教授|研究员|副研究员|讲师|助理教授|博士生导师|硕士生导师)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9._%+-]+\.[A-Za-z]{2,}")

FACULTY_HINTS = (
    "师资",
    "教师",
    "导师",
    "队伍",
    "人员",
    "正高",
    "副高",
    "讲师",
    "jxjs",
    "jsjs",
    "jsdw",
    "szdw",
    "dsdw",
    "sddw",
    "szdw1",
    "dsjs",
    "bssds",
    "sssds",
    "cyds",
    "jxms",
    "yxrc",
    "zlkxj",
    "ljrc",
    "zyrc",
)

BAD_LINK_HINTS = (
    "招生",
    "招聘",
    "新闻",
    "通知",
    "公告",
    "动态",
    "学生",
    "校友",
    "下载",
    "党",
    "工会",
    "概况",
    "简介",
    "领导",
    "首页",
)

STRONG_FACULTY_PATH_HINTS = (
    "/szdw/",
    "/sddw/",
    "/szdw1/",
    "/jsjs",
    "/jsdw",
    "/dsdw",
    "/dsjs",
    "/bssds",
    "/sssds",
    "/cyds",
    "/jxms",
    "/yxrc",
    "/zlkxj",
    "/ljrc",
    "/zyrc",
    "/gccrc",
    "/bshldz",
)

EXCLUDED_PAGE_PATH_HINTS = (
    "/xygk/",
    "/xsgz/",
    "/djgz/",
    "/kxyj/",
    "/jgsz/",
    "/info/",
    "/index",
    "/rczp",
    "/zp",
)

NAME_STOPWORDS = {
    "首页",
    "学校首页",
    "设为首页",
    "加入收藏",
    "站内搜索",
    "师资队伍",
    "师资概况",
    "队伍概况",
    "优秀人才",
    "领军人才",
    "卓越人才",
    "教学名师",
    "战略科学家",
    "教师介绍",
    "教师队伍",
    "导师队伍",
    "建筑系教师",
    "规划系教师",
    "环境工程系",
    "环境科学系",
    "专业课教师",
    "数理教师",
    "英语教师",
    "思政教师",
    "正高职称",
    "副高职称",
    "中级职称",
    "招生办公室",
    "科学研究",
    "国际交流",
    "现任领导",
    "机构设置",
    "党建工作",
    "组织架构",
    "联系我们",
    "学生工作",
    "跳转",
    "学院简介",
    "学院概况",
    "本科生教育",
    "研究生教育",
    "人才培养",
    "信息公开",
    "校园邮箱",
    "网关入口",
    "图书馆",
    "下页",
    "尾页",
    "上页",
    "上一页",
    "下一页",
    "更多",
    "详细",
    "学工动态",
    "党建动态",
    "平台基地",
    "学科建设",
    "科研动态",
    "新闻动态",
    "组织机构",
    "科研获奖",
    "科研成果",
    "招聘信息",
    "科研基地",
    "议事机构",
    "服务指南",
    "本科生",
    "研究生",
    "公备",
    "工会教代会",
    "学术活动",
    "合作项目",
    "教工之家",
    "团学组织",
    "生物医学工程",
    "新闻资讯",
    "学工网",
    "工大新闻网",
    "科研项目",
    "党建品牌",
    "科研平台",
    "办公服务",
    "光学工程",
    "就创服务",
    "本科项目",
    "科研信息",
    "学术会议",
    "学术委员会",
    "人民中国杯",
    "卡西欧杯",
    "外研社杯",
    "副教授",
    "副理事长",
    "十二五",
}

NAME_BAD_SUFFIXES = (
    "系",
    "部",
    "处",
    "室",
    "办",
    "中心",
    "学院",
    "团队",
    "研究",
    "交流",
    "领导",
    "设置",
    "工作",
    "架构",
    "联系",
    "学生",
    "跳转",
    "首页",
    "下页",
    "尾页",
    "概况",
    "简介",
    "教育",
    "培养",
    "公开",
    "邮箱",
    "图书馆",
    "入口",
    "动态",
    "基地",
    "建设",
    "获奖",
    "成果",
    "招聘",
    "机构",
    "指南",
    "本科",
    "项目",
    "平台",
    "服务",
    "资讯",
    "新闻",
    "工程",
    "活动",
    "组织",
    "教代会",
    "工会",
    "教工",
    "品牌",
    "学术",
    "合作",
    "情况",
    "医学",
    "工大",
    "学工",
    "科研",
    "会议",
    "委员会",
    "理事",
    "教授",
    "研究员",
    "讲师",
    "助教",
    "职称",
    "中国杯",
)

NAME_BAD_TOKENS = (
    "学院",
    "中心",
    "队伍",
    "概况",
    "介绍",
    "教师",
    "导师",
    "人才",
    "名师",
    "职称",
    "首页",
    "招生",
    "研究",
    "交流",
    "领导",
    "设置",
    "工作",
    "架构",
    "联系",
    "学生",
    "跳转",
    "教育",
    "培养",
    "公开",
    "邮箱",
    "图书馆",
    "入口",
)


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def valid_person_name(name: str) -> bool:
    name = clean(name)
    if not NAME_RE.fullmatch(name):
        return False
    if name in NAME_STOPWORDS:
        return False
    if any(token in name for token in NAME_BAD_TOKENS):
        return False
    if name.endswith(NAME_BAD_SUFFIXES):
        return False
    return True


def normalize_url(url: str) -> str:
    return urldefrag(url)[0].rstrip("/")


def same_or_sub_domain(url: str, base_url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    return host == base_host or host.endswith("." + base_host)


def is_strong_faculty_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    if not path or path == "/":
        return False
    if any(hint in path for hint in EXCLUDED_PAGE_PATH_HINTS):
        return False
    return any(hint in path for hint in STRONG_FACULTY_PATH_HINTS)


def is_profile_href(url: str) -> bool:
    return "/info/" in urlparse(url).path.lower()


def fetch(session: requests.Session, url: str, sleep: float) -> str:
    resp = session.get(url, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    time.sleep(sleep)
    return resp.text


def is_faculty_like_link(text: str, href: str) -> bool:
    hay = f"{text} {href}".lower()
    if any(bad in text for bad in BAD_LINK_HINTS):
        return False
    return any(hint.lower() in hay for hint in FACULTY_HINTS)


def discover_faculty_pages(session: requests.Session, department_url: str, max_pages: int, sleep: float) -> list[str]:
    seen: set[str] = set()
    found: dict[str, int] = {}
    queue = deque([(department_url, 0)])
    while queue and len(seen) < max_pages:
        url, depth = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        try:
            html = fetch(session, url, sleep)
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        page_text = clean(soup.get_text(" ", strip=True))
        if any(hint in page_text[:3000] for hint in ["师资队伍", "教师介绍", "教师队伍", "导师队伍"]):
            found[url] = max(found.get(url, 0), 10)
        for a in soup.find_all("a"):
            text = clean(a.get_text(" ", strip=True))
            href = clean(a.get("href") or "")
            if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                continue
            if any(token in href for token in ["<", ">", "转换链接错误"]):
                continue
            next_url = normalize_url(urljoin(url, href))
            if not same_or_sub_domain(next_url, department_url):
                continue
            strong = is_strong_faculty_page(next_url)
            if strong and (is_faculty_like_link(text, href) or is_strong_faculty_page(url)):
                found[next_url] = max(found.get(next_url, 0), 100 if any(x in text for x in ["教师", "师资", "导师"]) else 60)
                if depth < 1:
                    queue.append((next_url, depth + 1))
            elif depth == 0 and any(x in text for x in ["师资队伍", "教师队伍", "导师队伍"]):
                queue.append((next_url, depth + 1))
    return [url for url, _ in sorted(found.items(), key=lambda item: (-item[1], item[0])) if is_strong_faculty_page(url)]


def page_section_label(soup: BeautifulSoup) -> str:
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    return re.split(r"[-_|]", title, maxsplit=1)[0]


def extract_context_title(text: str) -> str:
    m = TITLE_RE.search(text[:500])
    return clean(m.group(1)) if m else ""


def extract_teacher_rows(session: requests.Session, department: str, page_url: str, sleep: float) -> list[dict]:
    if not is_strong_faculty_page(page_url):
        return []
    try:
        html = fetch(session, page_url, sleep)
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    section = page_section_label(soup)
    rows: list[dict] = []
    seen_names: set[tuple[str, str]] = set()
    for a in soup.find_all("a"):
        name = clean(a.get_text(" ", strip=True))
        if not valid_person_name(name):
            continue
        href = clean(a.get("href") or "")
        profile_url = ""
        if href and not href.startswith(("javascript:", "mailto:", "tel:", "#")):
            profile_url = normalize_url(urljoin(page_url, href))
            if not same_or_sub_domain(profile_url, page_url) and not same_or_sub_domain(profile_url, "https://www.bjut.edu.cn/"):
                profile_url = ""
            if profile_url and not is_profile_href(profile_url):
                continue
        parent_text = clean(a.parent.get_text(" ", strip=True) if a.parent else "")
        key = (department, name)
        if key in seen_names:
            continue
        seen_names.add(key)
        rows.append(
            {
                "school_name": "北京工业大学",
                "department": department,
                "teacher_name": name,
                "title": extract_context_title(parent_text) or extract_context_title(section),
                "research_fields": "",
                "teacher_unit": department,
                "email": "",
                "teacher_profile_url": profile_url,
                "source_url": page_url,
                "source_section": section,
                "confidence": "0.78" if profile_url else "0.68",
                "extract_method": "bjut_faculty_list_anchor",
            }
        )
    for line in soup.get_text("\n", strip=True).splitlines():
        name = clean(line)
        if not valid_person_name(name):
            continue
        key = (department, name)
        if key in seen_names:
            continue
        seen_names.add(key)
        rows.append(
            {
                "school_name": "北京工业大学",
                "department": department,
                "teacher_name": name,
                "title": extract_context_title(section),
                "research_fields": "",
                "teacher_unit": department,
                "email": "",
                "teacher_profile_url": "",
                "source_url": page_url,
                "source_section": section,
                "confidence": "0.62",
                "extract_method": "bjut_faculty_list_text",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse BJUT department faculty pages into CSV")
    parser.add_argument("--departments-csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-pages-per-department", type=int, default=12)
    parser.add_argument("--sleep", type=float, default=0.1)
    args = parser.parse_args()

    departments = pd.read_csv(args.departments_csv).fillna("")
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": USER_AGENT})

    all_rows: list[dict] = []
    discovered_rows: list[dict] = []
    for _, row in departments.iterrows():
        department = clean(row.get("department"))
        site_url = clean(row.get("site_url"))
        if not department or not site_url.startswith(("http://", "https://")):
            continue
        pages = discover_faculty_pages(session, site_url, args.max_pages_per_department, args.sleep)
        for page in pages:
            discovered_rows.append({"department": department, "faculty_page_url": page})
            all_rows.extend(extract_teacher_rows(session, department, page, args.sleep))

    dedup: dict[tuple[str, str], dict] = {}
    for row in all_rows:
        key = (row["department"], row["teacher_name"])
        previous = dedup.get(key)
        if previous is None:
            dedup[key] = row
            continue
        prev_has_profile = bool(previous["teacher_profile_url"])
        row_has_profile = bool(row["teacher_profile_url"])
        prev_is_mentor = any(token in previous["source_url"] for token in ["/bssds", "/sssds", "/cyds", "/dsdw", "/dsjs"])
        row_is_mentor = any(token in row["source_url"] for token in ["/bssds", "/sssds", "/cyds", "/dsdw", "/dsjs"])
        if row_has_profile and not prev_has_profile:
            chosen = row
        elif row_has_profile == prev_has_profile and prev_is_mentor and not row_is_mentor:
            chosen = row
        else:
            chosen = previous
        titles = []
        for title in [clean(previous.get("title")), clean(row.get("title"))]:
            titles.extend(part for part in title.split("；") if part)
        merged_title = "；".join(dict.fromkeys(title for title in titles if title))
        chosen["title"] = merged_title
        dedup[key] = chosen
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "school_name",
        "department",
        "teacher_name",
        "title",
        "research_fields",
        "teacher_unit",
        "email",
        "teacher_profile_url",
        "source_url",
        "source_section",
        "confidence",
        "extract_method",
    ]
    with output.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(dedup.values(), key=lambda item: (item["department"], item["teacher_name"], item["teacher_profile_url"])))
    discovered_path = output.with_name(output.stem + "_师资页发现.csv")
    with discovered_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["department", "faculty_page_url"])
        writer.writeheader()
        writer.writerows(discovered_rows)
    print(f"wrote {output}; teachers={len(dedup)}; faculty_pages={len(discovered_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
