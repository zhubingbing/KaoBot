#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import csv
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import ProxyHandler, Request, build_opener

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", os.getcwd())

from crawl4ai_docker_fetch import fetch_url_with_docker

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
except Exception:
    AsyncWebCrawler = None
    BrowserConfig = None
    CacheMode = None
    CrawlerRunConfig = None
    PruningContentFilter = None
    DefaultMarkdownGenerator = None


TEACHER_FIELDS = [
    "school_name", "department", "teacher_name", "title", "research_fields",
    "teacher_unit", "email", "teacher_profile_url", "source_url", "confidence", "extract_method",
]
SOURCE_FIELDS = ["school_name", "department", "department_site_url", "teacher_page_url", "score", "matched_keywords", "source_url"]
PROGRESS_FIELDS = [
    "school_name", "department", "department_site_url", "status", "teacher_pages",
    "teachers", "error", "finished_at",
]
OVERRIDE_FIELDS = ["school_name", "department", "url", "url_type", "mode", "notes"]

TEACHER_LINK_TEXT = [
    "师资", "教师", "教师队伍", "师资队伍", "师资情况", "教职工", "人员", "研究队伍", "导师", "院士",
    "faculty", "people", "staff", "teacher",
]
NEGATIVE_LINK_TEXT = ["招生", "招聘", "新闻", "通知", "公告", "校友", "学生", "下载", "登录", "系统", "搜索"]
PERSON_STOPWORDS = {
    "首页", "更多", "返回", "上一页", "下一页", "尾页", "教师", "师资", "院士", "学生",
    "学院简介", "教师队伍", "师资队伍", "师资情况", "工程技术人员", "退休人员",
    "清华大学", "材料学院", "学院教务信息化系统", "在职教师", "客座教授", "博士后队伍", "行政职员", "离退休教师",
    "人员情况", "图片列表", "筛选", "系主任信箱",
    "教师介绍", "人才招聘",
}
NAME_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,6}$")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def normalize_person_name(name: str) -> str:
    name = clean(name)
    name = re.sub(r"^(view|profile|read more)\s+", "", name, flags=re.I)
    if re.fullmatch(r"[\u4e00-\u9fa5· ]{2,12}", name):
        name = name.replace(" ", "")
    return name


def save_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path).fillna("")


def normalize_url(url: str, base_url: str) -> str:
    url = clean(url)
    if not url or url.startswith(("javascript:", "mailto:", "#")):
        return ""
    url = urljoin(base_url, url)
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed.geturl()


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def same_site(a: str, b: str) -> bool:
    return host(a) == host(b)


def fetch_urlopen(url: str) -> str:
    # Ignore local shell proxy variables; several runs failed because 127.0.0.1:7890 was not running.
    opener = build_opener(ProxyHandler({}))
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 school-intelligence-pipeline/0.1"})
    with opener.open(req, timeout=25) as resp:
        raw = resp.read()
        content_type = resp.headers.get("content-type", "")
    encoding = "utf-8"
    m = re.search(r"charset=([\w-]+)", content_type, re.I)
    if m:
        encoding = m.group(1)
    else:
        head = raw[:2000].decode("utf-8", errors="ignore")
        m = re.search(r"charset=[\"']?([\w-]+)", head, re.I)
        if m:
            encoding = m.group(1)
    return raw.decode(encoding, errors="ignore")


async def fetch_crawl4ai_once(url: str) -> str:
    if os.getenv("SCHOOL_PIPELINE_CRAWLER_ENGINE") == "crawl4ai_docker":
        return (await fetch_url_with_docker(url))["html"]
    if AsyncWebCrawler is None:
        raise RuntimeError("crawl4ai_not_available")
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
    return getattr(result, "html", "") or ""


def fetch(url: str, sleep: float = 0.05) -> tuple[str, str, str, str]:
    try:
        html = asyncio.run(fetch_crawl4ai_once(url))
        method = "crawl4ai"
        raw_markdown = ""
        fit_markdown = ""
    except Exception:
        if os.getenv("SCHOOL_PIPELINE_CRAWLER_ENGINE") == "crawl4ai_docker":
            raise
        html = fetch_urlopen(url)
        method = "urlopen_fallback"
        raw_markdown = ""
        fit_markdown = ""
    if sleep:
        time.sleep(sleep)
    return html, method, raw_markdown, fit_markdown


def teacher_link_score(text: str, url: str) -> tuple[int, list[str]]:
    hay = f"{text} {url}".lower()
    score = 0
    hits = []
    for token in TEACHER_LINK_TEXT:
        if token.lower() in hay:
            score += 15
            hits.append(token)
    if re.search(r"/(szdw|szqk|jsdw|jzyg|teacher|teachers|faculty|people|staff|ry)/?", hay):
        score += 20
        hits.append("teacher_url")
    for token in NEGATIVE_LINK_TEXT:
        if token.lower() in hay and token not in {"教师"}:
            score -= 10
    return score, hits


def discover_teacher_pages(home_html: str, department_site_url: str, max_pages: int) -> list[dict]:
    soup = BeautifulSoup(home_html, "html.parser")
    rows = []
    seen = set()
    for a in soup.find_all("a", href=True):
        text = clean(a.get("title") or a.get_text(" ", strip=True))
        url = normalize_url(a.get("href"), department_site_url)
        if not text or not url or not same_site(department_site_url, url):
            continue
        score, hits = teacher_link_score(text, url)
        if score < 15:
            continue
        key = url
        if key in seen:
            continue
        seen.add(key)
        rows.append({"url": url, "score": score, "matched_keywords": ";".join(dict.fromkeys(hits)), "title": text})
    rows.sort(key=lambda x: (-x["score"], x["url"]))
    return rows[:max_pages] if max_pages > 0 else rows


def valid_person_name(name: str) -> bool:
    name = normalize_person_name(name)
    if not name or len(name) > 40 or name in PERSON_STOPWORDS:
        return False
    if re.search(r"(系教师|教师介绍|人才招聘)$", name):
        return False
    if any(token in name for token in ["简介", "概况", "队伍", "人员", "情况", "更多", "首页", "系统", "联系方式"]):
        return False
    if re.fullmatch(r"[\u4e00-\u9fa5·]{2,6}", name):
        return True
    if re.fullmatch(r"[A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+){1,3}", name):
        return True
    return False


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def extract_name_from_anchor(a) -> str:
    for value in [a.get("title"), a.get_text(" ", strip=True)]:
        name = normalize_person_name(value)
        if valid_person_name(name):
            return name
    name_box = a.find(class_=re.compile(r"name", re.I))
    if name_box:
        name = clean(name_box.get_text(" ", strip=True))
        if valid_person_name(name):
            return name
    return ""


def official_root(url: str) -> str:
    parts = host(url).split(".")
    if len(parts) >= 3 and parts[-2:] == ["edu", "cn"]:
        return ".".join(parts[-3:])
    return host(url)


def likely_profile_url(url: str, source_url: str) -> bool:
    if not (same_site(url, source_url) or official_root(url) == official_root(source_url)):
        return False
    lower = url.lower()
    return bool(
        re.search(r"/info/\d+/\d+\.htm", lower)
        or re.search(r"/essay/\d+/\d+\.html", lower)
        or re.search(r"(teacher|faculty|people|staff|szdw|jsdw)", lower)
        or re.search(r"[?&]p=\d+\b", lower)
    )


def extract_teachers_from_script_arrays(school: str, department: str, html: str, source_url: str) -> list[dict]:
    rows = []
    seen = set()
    pattern = re.compile(
        r'"showTitle":"(?P<name>[^"]+)"'
        r'.{0,800}?'
        r'(?:\"yjly\":\"(?P<research>[^"]*)\")?'
        r'.{0,800}?'
        r'"url":\{"asString":"(?P<url>[^"]+)"\}',
        re.S,
    )
    for match in pattern.finditer(html):
        name = normalize_person_name(match.group("name"))
        if not valid_person_name(name):
            continue
        profile = normalize_url(match.group("url"), source_url)
        if not profile:
            continue
        key = (department, name, profile)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "school_name": school,
            "department": department,
            "teacher_name": name,
            "title": "",
            "research_fields": clean((match.group("research") or "").replace("\\/", "/").replace(",", "；")),
            "teacher_unit": department,
            "email": "",
            "teacher_profile_url": profile,
            "source_url": source_url,
            "confidence": "0.91",
            "extract_method": "embedded_js_teacher_data",
        })
    return rows


def extract_script_context(html: str, max_snippets: int = 12, max_chars: int = 12000) -> list[str]:
    snippets = []
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = script.get_text("\n", strip=True)
        if not text:
            continue
        if not any(token in text for token in ["showTitle", "qh_data", "teacher", "faculty", "导师", "教授", "yjly", "asString"]):
            continue
        compact = clean(text).replace("\\/", "/")
        if len(compact) > 1800:
            compact = compact[:1800]
        if compact and compact not in snippets:
            snippets.append(compact)
        if len(snippets) >= max_snippets:
            break
    total = 0
    kept = []
    for snippet in snippets:
        if total + len(snippet) > max_chars:
            break
        kept.append(snippet)
        total += len(snippet)
    return kept


def extract_teachers_from_page(school: str, department: str, html: str, source_url: str) -> list[dict]:
    script_rows = extract_teachers_from_script_arrays(school, department, html, source_url)
    if script_rows:
        return script_rows
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()
    for a in soup.find_all("a", href=True):
        profile = normalize_url(a.get("href"), source_url)
        if not profile:
            continue
        name = extract_name_from_anchor(a)
        if not name:
            continue
        if not likely_profile_url(profile, source_url):
            continue
        key = (department, name, profile)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "school_name": school,
            "department": department,
            "teacher_name": name,
            "title": "",
            "research_fields": "",
            "teacher_unit": department,
            "email": "",
            "teacher_profile_url": profile,
            "source_url": source_url,
            "confidence": "0.86",
            "extract_method": "department_site_teacher_list",
        })
    return rows


class TeacherPageLLMExtractor:
    def __init__(self, enable_ai: bool, model: str) -> None:
        load_dotenv()
        self.enable_ai = enable_ai
        self.model = model
        self.client = None
        if enable_ai and os.getenv("OPENAI_API_KEY"):
            kwargs = {"api_key": os.getenv("OPENAI_API_KEY")}
            if os.getenv("OPENAI_BASE_URL"):
                kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
            self.client = OpenAI(**kwargs)

    def available(self) -> bool:
        return self.client is not None

    def filter_teacher_candidates(self, school: str, department: str, source_url: str, candidates: list[dict]) -> list[dict]:
        if not self.client or not candidates:
            return candidates
        ambiguous = []
        keep = []
        for row in candidates:
            name = clean(row.get("teacher_name"))
            if re.search(r"(教师介绍|人才招聘|招聘|系教师|学系|研究所|中心|实验室|团队)$", name):
                ambiguous.append(row)
            elif "系" in name or "学院" in name or "中心" in name or "研究所" in name:
                ambiguous.append(row)
            else:
                keep.append(row)
        if not ambiguous:
            return candidates
        payload = {
            "task": "判断高校院系教师页中的候选条目到底是具体老师、教师分组入口，还是噪声栏目词。",
            "school_name": school,
            "department": department,
            "source_url": source_url,
            "candidates": [
                {
                    "teacher_name": clean(row.get("teacher_name")),
                    "teacher_profile_url": clean(row.get("teacher_profile_url")),
                }
                for row in ambiguous[:80]
            ],
            "labels": ["person", "teacher_group", "noise"],
            "rules": [
                "具体老师一般是自然人姓名，不是栏目名",
                "如 规划系教师、社会工作系、建筑系教师 这类一般是 teacher_group，不是 person",
                "如 人才招聘、教师介绍、更多、首页 这类一般是 noise",
                "只返回 JSON，不要解释",
            ],
            "return_json_schema": {
                "items": [
                    {
                        "teacher_name": "原始候选名",
                        "label": "person|teacher_group|noise"
                    }
                ]
            },
        }
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是高校教师页候选条目分类器。只返回严格 JSON。"},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0,
                timeout=30,
            )
            content = (resp.choices[0].message.content or "{}").strip().strip("`")
            data = json.loads(content)
        except Exception:
            return keep
        label_map = {
            clean(item.get("teacher_name")): clean(item.get("label")).lower()
            for item in data.get("items", [])
        }
        for row in ambiguous:
            label = label_map.get(clean(row.get("teacher_name")), "")
            if label == "person":
                keep.append(row)
        return keep

    def extract(self, school: str, department: str, html: str, source_url: str) -> list[dict]:
        if not self.client:
            return []
        soup = BeautifulSoup(html, "html.parser")
        anchors = []
        for a in soup.find_all("a", href=True):
            text = clean(a.get("title") or a.get_text(" ", strip=True))
            href = normalize_url(a.get("href"), source_url)
            if not text or not href:
                continue
            if len(anchors) >= 120:
                break
            anchors.append({"text": text[:80], "url": href})
        script_snippets = extract_script_context(html)
        prompt = {
            "task": "从高校院系教师页中抽取教师列表。页面可能把教师数据写在正文、链接区域，或写在 script 里的 JSON/JS 数组中。你必须综合页面正文、链接和脚本片段判断。",
            "school_name": school,
            "department": department,
            "source_url": source_url,
            "text_sample": page_text(html)[:5000],
            "anchor_candidates": anchors,
            "script_snippets": script_snippets,
            "return_json_schema": {
                "is_teacher_page": True,
                "teachers": [
                    {
                        "teacher_name": "姓名",
                        "title": "职称或空",
                        "research_fields": "研究方向或空",
                        "email": "邮箱或空",
                        "teacher_profile_url": "个人主页 URL 或空"
                    }
                ]
            },
            "rules": [
                "teacher_name 必须是中文姓名或常见英文姓名，不得返回栏目名",
                "如果脚本中有 showTitle/name/title/url/yjly 等字段，优先从脚本抽教师",
                "不要返回 在职教师、系主任信箱、人员情况、图片列表、筛选、研究所、研究领域、职称、高级/副高级/中级 这类栏目词",
                "如果无法确定 teacher_profile_url，可以留空",
                "只返回 JSON，不要解释",
            ],
        }
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是高校教师页信息抽取器。只返回严格 JSON。"},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                temperature=0,
                timeout=30,
            )
            content = (resp.choices[0].message.content or "{}").strip().strip("`")
            data = json.loads(content)
        except Exception:
            return []
        if not data.get("is_teacher_page"):
            return []
        rows = []
        seen = set()
        for item in data.get("teachers", []):
            name = normalize_person_name(item.get("teacher_name", ""))
            if not valid_person_name(name):
                continue
            profile = normalize_url(item.get("teacher_profile_url", ""), source_url) if item.get("teacher_profile_url") else ""
            key = (department, name, profile or source_url)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "school_name": school,
                "department": department,
                "teacher_name": name,
                "title": clean(item.get("title", "")),
                "research_fields": clean(item.get("research_fields", "")),
                "teacher_unit": department,
                "email": clean(item.get("email", "")),
                "teacher_profile_url": profile,
                "source_url": source_url,
                "confidence": "0.92",
                "extract_method": "llm_teacher_page_extract",
            })
        return rows


def load_departments(output_dir: Path, only_departments: set[str]) -> list[dict]:
    df = read_csv(output_dir / "departments.csv")
    if df.empty:
        raise SystemExit(f"缺少 departments.csv：{output_dir}")
    rows = []
    for _, row in df.iterrows():
        department = clean(row.get("department"))
        site_url = clean(row.get("site_url"))
        if only_departments and department not in only_departments:
            continue
        if not department or not site_url.startswith(("http://", "https://")):
            continue
        rows.append({"department": department, "site_url": site_url})
    return rows


def load_overrides(path: Path, school: str, only_departments: set[str]) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    df = read_csv(path)
    if df.empty:
        return {}
    rows = {}
    for _, row in df.iterrows():
        school_name = clean(row.get("school_name"))
        department = clean(row.get("department"))
        url = clean(row.get("url"))
        url_type = clean(row.get("url_type"))
        mode = clean(row.get("mode")) or "append"
        if school_name != school or not department or not url:
            continue
        if only_departments and department not in only_departments:
            continue
        rows.setdefault(department, []).append({
            "school_name": school_name,
            "department": department,
            "url": url,
            "url_type": url_type,
            "mode": mode,
            "notes": clean(row.get("notes")),
        })
    return rows


def apply_department_site_override(item: dict, overrides: list[dict]) -> dict:
    site_rows = [row for row in overrides if row["url_type"] == "department_site"]
    if not site_rows:
        return item
    replace_rows = [row for row in site_rows if row["mode"] == "replace"]
    if replace_rows:
        chosen = replace_rows[-1]
    elif not clean(item.get("site_url")):
        chosen = site_rows[-1]
    else:
        return item
    updated = dict(item)
    updated["site_url"] = chosen["url"]
    return updated


def merge_teacher_page_candidates(discovered: list[dict], override_rows: list[dict], max_pages: int) -> list[dict]:
    rows = []
    if any(row["mode"] == "replace" and row["url_type"] in {"teacher_hub", "teacher_group"} for row in override_rows):
        discovered = []
    rows.extend(discovered)
    for row in override_rows:
        if row["url_type"] not in {"teacher_hub", "teacher_group"}:
            continue
        rows.append({
            "url": row["url"],
            "score": 999 if row["url_type"] == "teacher_group" else 950,
            "matched_keywords": f"override:{row['url_type']}",
            "title": row["notes"] or row["url_type"],
        })
    dedup = {}
    for row in rows:
        dedup[clean(row["url"])] = row
    merged = sorted(dedup.values(), key=lambda x: (-int(x["score"]), x["url"]))
    return merged[:max_pages] if max_pages > 0 else merged


def merge_existing_rows(path: Path, rows: list[dict], department_names: set[str], key_fields: list[str]) -> list[dict]:
    if not path.exists():
        return rows
    try:
        existing = pd.read_csv(path).fillna("").to_dict(orient="records")
    except Exception:
        return rows
    kept = [row for row in existing if clean(row.get("department")) not in department_names]
    merged = kept + rows
    dedup = {}
    for row in merged:
        key = tuple(clean(row.get(field, "")) for field in key_fields)
        dedup[key] = row
    return list(dedup.values())


def process_department(
    item: dict,
    school: str,
    teacher_pages_per_department: int,
    sleep: float,
    enable_ai: bool,
    model: str,
    department_overrides: list[dict],
) -> dict:
    department = item["department"]
    item = apply_department_site_override(item, department_overrides)
    site_url = item["site_url"]
    llm_extractor = TeacherPageLLMExtractor(enable_ai=enable_ai, model=model)
    result = {
        "department": department,
        "site_url": site_url,
        "teachers": [],
        "source_rows": [],
        "issue": None,
    }
    try:
        home_html, _, _, _ = fetch(site_url, sleep)
        teacher_pages = discover_teacher_pages(home_html, site_url, teacher_pages_per_department)
        teacher_pages = merge_teacher_page_candidates(teacher_pages, department_overrides, teacher_pages_per_department)
        for page in teacher_pages:
            page_html, _, _, _ = fetch(page["url"], sleep)
            extracted = []
            if llm_extractor.available():
                extracted.extend(llm_extractor.extract(school, department, page_html, page["url"]))
            if not extracted:
                rule_rows = extract_teachers_from_page(school, department, page_html, page["url"])
                if llm_extractor.available():
                    rule_rows = llm_extractor.filter_teacher_candidates(school, department, page["url"], rule_rows)
                seen_keys = {(row["teacher_name"], row.get("teacher_profile_url", "")) for row in extracted}
                for row in rule_rows:
                    key = (row["teacher_name"], row.get("teacher_profile_url", ""))
                    if key not in seen_keys:
                        extracted.append(row)
            result["teachers"].extend(extracted)
            result["source_rows"].append({
                "school_name": school,
                "department": department,
                "department_site_url": site_url,
                "teacher_page_url": page["url"],
                "score": page["score"],
                "matched_keywords": page["matched_keywords"],
                "source_url": site_url,
            })
    except Exception as exc:
        result["issue"] = {"department": department, "site_url": site_url, "error": str(exc)[:300]}
    return result


def save_progress(path: Path, rows: list[dict]) -> None:
    save_csv(path, rows, PROGRESS_FIELDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="从院系官网自动发现师资页并抽取教师列表")
    parser.add_argument("--school", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-departments", type=int, default=0)
    parser.add_argument("--teacher-pages-per-department", type=int, default=2)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--enable-ai", action="store_true")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--overrides", default="configs/department_overrides.csv")
    parser.add_argument("--only-department", action="append", default=[])
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    only = {clean(x) for x in args.only_department if clean(x)}
    departments = load_departments(out_dir, only)
    override_map = load_overrides(Path(args.overrides), args.school, only)
    target_departments = {item["department"] for item in departments}
    if args.max_departments > 0:
        departments = departments[:args.max_departments]

    teachers = []
    source_rows = []
    issues = []
    progress_rows = []
    teacher_map = {}
    workers = max(1, args.workers)
    total = len(departments)
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(
                process_department,
                item,
                args.school,
                args.teacher_pages_per_department,
                args.sleep,
                args.enable_ai,
                args.model,
                override_map.get(item["department"], []),
            ): item
            for item in departments
        }
        for future in as_completed(future_map):
            item = future_map[future]
            department = item["department"]
            site_url = item["site_url"]
            completed += 1
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "department": department,
                    "site_url": site_url,
                    "teachers": [],
                    "source_rows": [],
                    "issue": {"department": department, "site_url": site_url, "error": str(exc)[:300]},
                }

            for row in result["teachers"]:
                teacher_map.setdefault((row["department"], row["teacher_name"]), row)
            source_rows.extend(result["source_rows"])
            if result["issue"]:
                issues.append(result["issue"])

            progress_rows.append({
                "school_name": args.school,
                "department": department,
                "department_site_url": site_url,
                "status": "error" if result["issue"] else "done",
                "teacher_pages": len(result["source_rows"]),
                "teachers": len(result["teachers"]),
                "error": result["issue"]["error"] if result["issue"] else "",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            })

            teachers = list(teacher_map.values())
            merged_teachers = merge_existing_rows(
                out_dir / "teachers.csv",
                teachers,
                target_departments,
                ["department", "teacher_name", "teacher_profile_url"],
            )
            merged_sources = merge_existing_rows(
                out_dir / "department_teacher_sources.csv",
                source_rows,
                target_departments,
                ["department", "teacher_page_url"],
            )
            merged_progress = merge_existing_rows(
                out_dir / "department_teacher_progress.csv",
                progress_rows,
                target_departments,
                ["department", "department_site_url"],
            )
            save_csv(out_dir / "teachers.csv", merged_teachers, TEACHER_FIELDS)
            save_csv(out_dir / "department_teacher_sources.csv", merged_sources, SOURCE_FIELDS)
            save_progress(out_dir / "department_teacher_progress.csv", merged_progress)
            if issues:
                merged_issues = merge_existing_rows(
                    out_dir / "department_teacher_issues.csv",
                    issues,
                    target_departments,
                    ["department", "site_url", "error"],
                )
                save_csv(out_dir / "department_teacher_issues.csv", merged_issues, ["department", "site_url", "error"])

            print(
                f"[{completed}/{total}] {department} "
                f"teacher_pages={len(result['source_rows'])} "
                f"teachers={len(result['teachers'])} "
                f"status={'error' if result['issue'] else 'done'}",
                flush=True,
            )

    teachers = list(teacher_map.values())

    summary = [
        f"# {args.school} 院系官网教师发现结果",
        "",
        f"- 院系数：{len(departments)}",
        f"- 教师页：{len(source_rows)}",
        f"- 教师记录：{len(teachers)}",
        f"- 并发 worker：{workers}",
    ]
    for dept, count in pd.DataFrame(teachers).groupby("department").size().sort_values(ascending=False).head(30).items() if teachers else []:
        summary.append(f"- {dept}：{count}")
    (out_dir / "department_teacher_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print({"departments": len(departments), "teacher_pages": len(source_rows), "teachers": len(teachers), "issues": len(issues)})


if __name__ == "__main__":
    main()
