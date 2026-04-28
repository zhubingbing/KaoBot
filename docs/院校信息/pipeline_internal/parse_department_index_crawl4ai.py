#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import csv
import os
import re
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", os.getcwd())

from crawl4ai_docker_fetch import fetch_url_with_docker

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
except Exception as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(f"crawl4ai 不可用，请先安装/升级 crawl4ai：{exc}")


DEPARTMENT_FIELDS = ["school_name", "department", "division", "site_url", "source_url", "confidence", "extract_method"]
CANDIDATE_FIELDS = ["school_name", "source_url", "depth", "title", "url", "score", "matched_keywords", "discovery_method"]
TEACHER_FIELDS = [
    "school_name", "department", "teacher_name", "title", "research_fields",
    "teacher_unit", "email", "teacher_profile_url", "source_url", "confidence", "extract_method",
]
UNIFIED_TEACHER_FIELDS = [
    "school_name", "teacher_name", "department", "level", "major_name", "research_direction",
    "teacher_title", "teacher_research_fields", "teacher_unit", "teacher_profile_url", "match_type", "source_url",
]
UNIFIED_PROGRAM_FIELDS = [
    "school_name", "level", "catalog_type", "department", "major_code", "major_name", "degree_type",
    "direction_code", "study_mode", "research_direction", "teacher_name", "teacher_profile_url",
    "enrollment_plan", "exam_subjects", "admissions_note", "source_url",
]


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def save_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def normalize_url(url: str, base_url: str) -> str:
    url = urljoin(base_url, clean(url))
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed.geturl()


def valid_department_name(name: str) -> bool:
    name = clean(name)
    if not name or len(name) > 36:
        return False
    bad_exact = {
        "学院概况", "学院简介", "院系设置", "院系概况", "院系纵览", "学校概况", "学校简介",
        "科学研究", "人才培养", "教育教学", "招生就业", "招生信息", "新闻中心", "通知公告",
        "合作交流", "党群工作", "机构设置", "联系我们", "校友会", "信息公开", "English",
        "本科生", "研究生", "博士生", "博士后", "师资队伍", "教师队伍", "教职工",
        "奖助体系", "校地合作研究院",
    }
    if name in bad_exact:
        return False
    if re.search(r"20\d{2}", name):
        return False
    if any(token in name for token in ["更多", "首页", "新闻", "通知", "公告", "招聘", "下载", "登录", "邮箱"]):
        return False
    if re.search(r"(学院|学系|研究院|研究所|中心|学部|书院|体育部|医院)$", name):
        return True
    return bool(re.search(r"(?<!体)系$", name))


def normalize_department_title(title: str) -> str:
    title = clean(title)
    title = re.sub(r"^[·•\-\s*_]+", "", title)
    title = re.sub(r"[\s*_]+$", "", title)
    title = clean(title)
    if not valid_department_name(title):
        short = clean(re.sub(r"[（(][^）)]{1,30}[）)]$", "", title))
        if valid_department_name(short):
            return short
    return title


def link_score(name: str, url: str) -> int:
    score = 60 if valid_department_name(name) else 0
    lower = url.lower()
    if any(token in lower for token in ["yxsz", "college", "department", "school", "faculty"]):
        score += 10
    if re.search(r"(news|notice|info|xw|tzgg|download|login)", lower):
        score -= 25
    return score


def extract_department_links(school: str, html: str, entry_url: str, method: str) -> tuple[list[dict], list[dict]]:
    rows = []
    candidates = []
    seen_departments = set()
    seen_candidates = set()

    def add_link(title: str, href: str) -> None:
        title = normalize_department_title(title)
        url = normalize_url(href, entry_url)
        if not title or not url:
            return
        score = link_score(title, url)
        if score >= 10 and (title, url) not in seen_candidates:
            seen_candidates.add((title, url))
            candidates.append({
                "school_name": school,
                "source_url": entry_url,
                "depth": 0,
                "title": title,
                "url": url,
                "score": score,
                "matched_keywords": "configured_department_index",
                "discovery_method": method,
            })
        if not valid_department_name(title):
            return
        key = (title, url)
        if key in seen_departments:
            return
        seen_departments.add(key)
        rows.append({
            "school_name": school,
            "department": title,
            "division": "",
            "site_url": url,
            "source_url": entry_url,
            "confidence": "0.93",
            "extract_method": method,
        })

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        add_link(a.get("title") or a.get_text(" ", strip=True), a.get("href"))
    for title, href in re.findall(r"\[([^\]]{1,80})\]\((https?://[^)\s]+|/[^)\s]+|[^)\s]+\.htm)\)", html):
        add_link(title, href)
    return rows, candidates


async def crawl_entry_once(url: str) -> dict:
    if os.getenv("SCHOOL_PIPELINE_CRAWLER_ENGINE") == "crawl4ai_docker":
        return await fetch_url_with_docker(url)
    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        verbose=False,
        channel=os.getenv("SCHOOL_PIPELINE_CHROME_CHANNEL", "chromium").strip() or "chromium",
        chrome_channel=os.getenv("SCHOOL_PIPELINE_CHROME_CHANNEL", "chromium").strip() or "chromium",
        use_managed_browser=False,
        use_persistent_context=False,
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed", min_word_threshold=0)
        ),
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
    if not getattr(result, "success", False):
        raise RuntimeError(getattr(result, "error_message", "") or "crawl4ai_failed")
    markdown = getattr(result, "markdown", "") or ""
    if hasattr(markdown, "raw_markdown"):
        raw_markdown = getattr(markdown, "raw_markdown", "") or ""
        fit_markdown = getattr(markdown, "fit_markdown", "") or ""
    else:
        raw_markdown = str(markdown or "")
        fit_markdown = ""
    return {
        "html": getattr(result, "html", "") or "",
        "raw_markdown": raw_markdown,
        "fit_markdown": fit_markdown,
    }


async def crawl_entry(url: str) -> dict:
    last_error = None
    for _ in range(3):
        try:
            return await crawl_entry_once(url)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1)
    raise RuntimeError(f"crawl4ai_failed_after_retries: {last_error}")


def write_summary(out_dir: Path, school: str, entry_url: str, departments: list[dict], candidates: list[dict], markdown_len: int) -> None:
    lines = [
        f"# {school} 院系入口解析结果",
        "",
        f"- 入口：{entry_url}",
        f"- 院系/机构链接：{len(departments)}",
        f"- 候选链接：{len(candidates)}",
        f"- Crawl4AI Markdown 长度：{markdown_len}",
        "",
        "## 院系样例",
    ]
    for row in departments[:40]:
        lines.append(f"- {row['department']}：{row['site_url']}")
    if len(departments) > 40:
        lines.append(f"- 其余 {len(departments) - 40} 条略。")
    (out_dir / "department_index_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl4AI 解析配置入口页中的院系/机构链接")
    parser.add_argument("--school", required=True)
    parser.add_argument("--entry-url", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-departments", type=int, default=0)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        crawled = asyncio.run(crawl_entry(args.entry_url))
    except Exception:
        cached = out_dir / "entry_raw.md"
        if not cached.exists():
            raise
        raw = cached.read_text(encoding="utf-8")
        crawled = {"html": raw, "raw_markdown": raw, "fit_markdown": ""}
    method = "crawl4ai_department_index"
    departments, candidates = extract_department_links(args.school, crawled["html"], args.entry_url, method)
    if args.max_departments > 0:
        departments = departments[:args.max_departments]

    save_csv(out_dir / "departments.csv", departments, DEPARTMENT_FIELDS)
    save_csv(out_dir / "discovered_departments.csv", departments, DEPARTMENT_FIELDS)
    save_csv(out_dir / "candidate_pages.csv", candidates, CANDIDATE_FIELDS)
    save_csv(out_dir / "teachers.csv", [], TEACHER_FIELDS)
    save_csv(out_dir / "unified_teachers.csv", [], UNIFIED_TEACHER_FIELDS)
    save_csv(out_dir / "program_teacher_links.csv", [], ["school_name", "department", "major_name", "research_direction", "teacher_name", "match_type", "confidence", "source_url"])
    save_csv(out_dir / "unified_programs.csv", [], UNIFIED_PROGRAM_FIELDS)
    (out_dir / "entry_raw.md").write_text(crawled["raw_markdown"], encoding="utf-8")
    (out_dir / "entry_fit.md").write_text(crawled["fit_markdown"], encoding="utf-8")
    write_summary(out_dir, args.school, args.entry_url, departments, candidates, len(crawled["raw_markdown"]))
    print({"departments": len(departments), "candidates": len(candidates), "output_dir": str(out_dir)})


if __name__ == "__main__":
    main()
