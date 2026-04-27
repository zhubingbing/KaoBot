#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urldefrag

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", os.getcwd())

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

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) school-intelligence-pipeline/0.1"
PAGE_TYPES = ["department", "program", "teacher_list", "teacher_profile", "admission", "news", "other"]
TEACHER_KEYWORDS = ["教师", "师资", "导师", "教授", "人员", "队伍", "教研", "faculty", "teacher", "staff", "people"]
PROGRAM_KEYWORDS = ["专业", "招生", "培养", "学科", "研究生", "本科", "目录", "program", "major", "admission"]
DEPARTMENT_KEYWORDS = ["院系", "学院", "学部", "系所", "招生学院", "院所", "department", "school", "college"]
NEGATIVE_KEYWORDS = ["新闻", "动态", "通知", "公告", "讲座", "会议", "登录", "邮箱", "图书馆", "校友", "招聘"]
NAME_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,6}$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+\s*(?:@| at )\s*[A-Za-z0-9._%+-]+(?:\s*(?:\.| dot )\s*[A-Za-z]{2,})+")
TITLE_RE = re.compile(r"(教授(?:、[^\s；;，,]+)?|副教授|研究员(?:/助理教授)?|副研究员|助理教授|讲师|预聘副教授[^\s；;，,]*|长聘副教授[^\s；;，,]*|院士)")
NAV_NAME_STOPWORDS = {
    "首页", "教师", "院士", "招生", "更多", "返回", "新闻", "学科", "确定", "学生", "门户",
    "本科生", "研究生", "理学部", "工学部", "人文学部", "师资队伍", "科研成果", "国际交流",
    "菁菁校园", "博士生导师", "北大概况", "北大简介", "学部与院系", "教育教学",
    "账号登录", "科学研究", "继续教育", "学院概况", "联系我们", "北京大学", "办公办事",
    "生活指南", "教学科研", "人才培养", "合作交流", "党群工作", "通知公告", "科研动态",
    "精品课程", "所中心", "机构设置", "现任领导", "行政人员", "教辅人员",
    "忘记密码", "学术讲座", "教育部", "院长寄语", "重大项目", "使用说明", "科研奖励", "院长致辞",
    "访问学者", "历史沿革",
    "教授", "副教授", "讲师", "博士后", "兼职教授", "博导列表", "硕导介绍", "常用资源", "学术活动",
    "校园热点", "工大要闻", "新闻资讯", "新闻动态", "交流动态", "快速通道", "研途有您",
    "教师队伍", "师资概况", "导师队伍", "教师介绍", "建筑系教师", "规划系教师", "战略科学家",
    "友情链接", "设为首页", "学校首页", "访客",
    "奋进双一流", "外媒关注", "内网门户", "工业设计系", "学生园地", "领军人才", "教学名师", "卓越人才",
}

CANDIDATE_FIELDS = ["school_name", "source_url", "depth", "title", "url", "score", "matched_keywords", "discovery_method"]
CLASSIFICATION_FIELDS = ["school_name", "url", "title", "page_type", "confidence", "reason", "classifier"]
DEPARTMENT_FIELDS = ["school_name", "department", "division", "site_url", "source_url", "confidence", "extract_method"]
PROGRAM_FIELDS = ["school_name", "department", "level", "major_code", "major_name", "research_direction", "source_url", "confidence", "extract_method"]
TEACHER_FIELDS = ["school_name", "department", "teacher_name", "title", "research_fields", "teacher_unit", "email", "teacher_profile_url", "source_url", "confidence", "extract_method"]
PROGRAM_TEACHER_LINK_FIELDS = ["school_name", "department", "major_name", "research_direction", "teacher_name", "match_type", "confidence", "source_url"]
UNIFIED_TEACHER_FIELDS = ["school_name", "teacher_name", "department", "level", "major_name", "research_direction", "teacher_title", "teacher_research_fields", "teacher_unit", "teacher_profile_url", "match_type", "source_url"]
ISSUE_FIELDS = ["school_name", "url", "stage", "issue_type", "message"]


def should_run_pdf_adapter(candidates: list[dict], classifications: list[dict]) -> bool:
    haystack = " ".join(
        [str(row.get("url", "")) + " " + str(row.get("title", "")) for row in candidates + classifications]
    )
    return any(token in haystack for token in ["招生", "专业目录", "招生目录", "硕士", "博士", "研究生", ".pdf", "admission", "zyml"])


def run_pdf_adapter(school_name: str, out_dir: Path, refresh: bool = False, max_pages: int = 120) -> dict:
    cmd = [
        sys.executable,
        str(Path(__file__).with_name("parse_admission_pdfs.py")),
        "--school-name",
        school_name,
        "--output-dir",
        str(out_dir),
        "--candidate-pages",
        str(out_dir / "candidate_pages.csv"),
        "--max-pages",
        str(max_pages),
        "--target-year",
        str(datetime.now().year),
    ]
    if refresh:
        cmd.append("--refresh")
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return {"adapter": "generic_admission_pdf", "status": "ok", "message": (proc.stdout or "").strip()}


def clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def norm_url(url: str, base: str = "") -> str:
    if base:
        url = urljoin(base, url)
    url, _ = urldefrag(url)
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme == "http" and (parsed.netloc.endswith(".edu.cn") or parsed.netloc.endswith(".edu")):
        return parsed._replace(scheme="https").geturl()
    return url


def same_or_sub_domain(seed: str, target: str) -> bool:
    a = urlparse(seed).netloc.lower()
    b = urlparse(target).netloc.lower()
    return b == a or b.endswith("." + a)




def official_root_host(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2:] == ["edu", "cn"]:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def related_official_url(seed: str, target: str) -> bool:
    if not target.startswith(("http://", "https://")):
        return False
    return official_root_host(seed) == official_root_host(target)


def is_valid_department_name(name: str) -> bool:
    name = clean(name)
    if not name or len(name) > 32:
        return False
    bad_exact = {
        "学院概况", "学院简介", "学院大事记", "现任领导", "机构设置", "联系我们", "人才培养",
        "科学研究", "党群工作", "合作交流", "招生就业", "新闻中心", "通知公告", "资料下载",
        "本科生", "研究生", "博士生导师", "硕士生导师", "产业导师", "队伍概况", "优秀人才",
    }
    if name in bad_exact:
        return False
    if any(token in name for token in ["公告", "新闻", "通知", "招标", "采购", "服务", "更多", "首页"]):
        return False
    return bool(re.search(r"(学院|学系|研究院|研究所|学部|中心|系)$", name))

def save_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})




def is_document_url(url: str) -> bool:
    lower = clean(url).lower()
    return lower.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")) or "/system/_content/download" in lower

def keyword_score(title: str, url: str) -> tuple[int, list[str]]:
    text = f"{title} {url}".lower()
    hits = []
    score = 0
    for word in TEACHER_KEYWORDS:
        if word.lower() in text:
            hits.append(word)
            score += 10
    for word in PROGRAM_KEYWORDS:
        if word.lower() in text:
            hits.append(word)
            score += 6
    for word in DEPARTMENT_KEYWORDS:
        if word.lower() in text:
            hits.append(word)
            score += 4
    if any(token in text for token in ["招生学院", "招生院系", "招生院所"]):
        hits.append("招生学院入口")
        score += 35
    if any(token in url.lower() for token in ["zsxy", "yxsz", "college", "department"]):
        hits.append("院系列表URL")
        score += 14
    if any(token in url.lower() for token in ["/szdw/", "/jsdw/", "/jzyg/", "/faculty", "/people", "/staff"]):
        hits.append("师资URL")
        score += 18
    if any(token in url.lower() for token in ["zzjzg", "/zmjs/", "teacher"]):
        hits.append("教师列表URL")
        score += 20
    for word in NEGATIVE_KEYWORDS:
        if word.lower() in text:
            score -= 5
    return score, hits


class Fetcher:
    def __init__(self, engine: str, sleep: float = 0.1) -> None:
        self.engine = engine
        self.sleep = sleep
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch_requests(self, url: str) -> str:
        resp = self.session.get(url, timeout=25, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or resp.encoding
        time.sleep(self.sleep)
        return resp.text

    async def fetch_crawl4ai_async(self, url: str) -> dict:
        if AsyncWebCrawler is None:
            raise RuntimeError("crawl4ai_not_available")
        browser_config = None
        if BrowserConfig is not None:
            chrome_channel = os.getenv("SCHOOL_PIPELINE_CHROME_CHANNEL", "").strip()
            try:
                if chrome_channel:
                    browser_config = BrowserConfig(
                        headless=True,
                        verbose=False,
                        channel=chrome_channel,
                        chrome_channel=chrome_channel,
                    )
                else:
                    browser_config = BrowserConfig(headless=True, verbose=False)
            except Exception:
                browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = None
        if CrawlerRunConfig is not None:
            kwargs = {}
            if CacheMode is not None:
                kwargs["cache_mode"] = CacheMode.BYPASS
            if DefaultMarkdownGenerator is not None and PruningContentFilter is not None:
                kwargs["markdown_generator"] = DefaultMarkdownGenerator(
                    content_filter=PruningContentFilter(
                        threshold=0.48,
                        threshold_type="fixed",
                        min_word_threshold=0,
                    )
                )
            run_config = CrawlerRunConfig(**kwargs)
        crawler = AsyncWebCrawler(config=browser_config) if browser_config else AsyncWebCrawler()
        async with crawler:
            result = await crawler.arun(url=url, config=run_config) if run_config else await crawler.arun(url=url)
        html = getattr(result, "html", "") or ""
        markdown = getattr(result, "markdown", "") or ""
        if hasattr(markdown, "raw_markdown"):
            raw_markdown = getattr(markdown, "raw_markdown", "") or ""
            fit_markdown = getattr(markdown, "fit_markdown", "") or ""
        else:
            raw_markdown = str(markdown or "")
            fit_markdown = ""
        time.sleep(self.sleep)
        return {"html": html or raw_markdown, "raw_markdown": raw_markdown, "fit_markdown": fit_markdown}

    def fetch(self, url: str) -> dict:
        if self.engine == "crawl4ai":
            return asyncio.run(self.fetch_crawl4ai_async(url))
        return {"html": self.fetch_requests(url), "raw_markdown": "", "fit_markdown": ""}


class PageClassifier:
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

    def rule_classify(self, title: str, url: str, text: str) -> dict:
        hay = f"{title} {url} {text[:1500]}".lower()
        scores = {t: 0 for t in PAGE_TYPES}
        scores["teacher_list"] += sum(8 for k in TEACHER_KEYWORDS if k.lower() in hay)
        scores["program"] += sum(7 for k in PROGRAM_KEYWORDS if k.lower() in hay)
        scores["department"] += sum(6 for k in DEPARTMENT_KEYWORDS if k.lower() in hay)
        scores["news"] += sum(6 for k in ["新闻", "通知", "公告", "讲座", "news"] if k.lower() in hay)
        teacher_field_hits = len(re.findall(r"职称|职\s*称|研究方向|研究领域|电子邮件|邮箱|办公电话", text))
        if teacher_field_hits:
            scores["teacher_profile"] += 10 + min(15, teacher_field_hits * 2)
            scores["teacher_list"] += min(20, teacher_field_hits * 2)
        if any(token in url.lower() for token in ["teacher", "faculty", "people", "szdw", "jsdw", "/sz/", "/jzyg/"]):
            scores["teacher_list"] += 8
        title_name = name_from_profile_title(title)
        if is_valid_person_name(title_name) and is_probable_teacher_profile_url(url):
            scores["teacher_profile"] += 28
        if "department" in url.lower() or "学部与院系" in hay or "院系设置" in hay:
            scores["department"] += 20
        page_type = max(scores, key=scores.get)
        confidence = min(0.95, max(0.25, scores[page_type] / 25)) if scores[page_type] else 0.2
        if page_type == "news" and ("教师" in hay or "教授" in hay) and scores["teacher_list"] >= scores["news"]:
            page_type = "teacher_list"
        if scores[page_type] <= 0:
            page_type = "other"
        return {"page_type": page_type, "confidence": round(confidence, 2), "reason": "rule_keyword_score", "classifier": "rule"}

    def ai_classify(self, title: str, url: str, text: str) -> Any:
        if not self.client:
            return None
        prompt = {
            "task": "Classify a Chinese university webpage for structured school data extraction.",
            "allowed_page_types": PAGE_TYPES,
            "url": url,
            "title": title,
            "text_sample": text[:2500],
            "return_json_schema": {"page_type": "one allowed type", "confidence": "0-1", "reason": "short Chinese reason"},
        }
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You classify webpages. Return strict JSON only."},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                temperature=0,
                timeout=20,
            )
            content = resp.choices[0].message.content or "{}"
            data = json.loads(content.strip().strip("`"))
            if data.get("page_type") in PAGE_TYPES:
                return {
                    "page_type": data.get("page_type"),
                    "confidence": float(data.get("confidence", 0.5)),
                    "reason": clean(data.get("reason", "ai")),
                    "classifier": "ai",
                }
        except Exception:
            return None
        return None

    def classify(self, title: str, url: str, text: str) -> dict:
        ai = self.ai_classify(title, url, text)
        if ai:
            return ai
        return self.rule_classify(title, url, text)

    def ai_rank_links(self, page_title: str, page_url: str, text: str, links: list[dict]) -> list[dict]:
        if not self.client or not links:
            return links
        sample = []
        for idx, link in enumerate(links[:80]):
            sample.append({
                "id": idx,
                "title": clean(link.get("title", "")),
                "url": clean(link.get("url", "")),
                "score": int(link.get("score", 0) or 0),
            })
        prompt = {
            "task": "从高校网页链接中识别与学校-院系-专业-教师采集有关的入口。",
            "page_title": page_title,
            "page_url": page_url,
            "page_text_sample": text[:1600],
            "link_candidates": sample,
            "link_types": ["department_site", "admission_department_list", "teacher_list", "program_catalog", "ignore"],
            "return_json_schema": {"links": [{"id": 0, "link_type": "department_site", "department": "学院名或空", "priority_boost": 0}]},
            "rules": [
                "学院/院系独立官网入口优先标为 department_site",
                "招生学院/招生院系列表标为 admission_department_list",
                "师资/教师/导师页面标为 teacher_list",
                "专业目录/招生目录/学科专业目录标为 program_catalog",
                "新闻通知公告和无关外站标为 ignore"
            ],
        }
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是高校官网信息抽取链接分类器。只返回严格 JSON。"},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                temperature=0,
                timeout=25,
            )
            data = json.loads((resp.choices[0].message.content or "{}").strip().strip("`"))
            decisions = {int(item.get("id")): item for item in data.get("links", []) if str(item.get("id", "")).isdigit()}
        except Exception:
            return links
        ranked = []
        for idx, link in enumerate(links):
            item = dict(link)
            decision = decisions.get(idx)
            if decision:
                link_type = clean(decision.get("link_type"))
                dept = clean(decision.get("department"))
                boost = int(float(decision.get("priority_boost", 0) or 0))
                if link_type and link_type != "ignore":
                    item["matched_keywords"] = clean(item.get("matched_keywords")) + ";llm:" + link_type
                    item["discovery_method"] = "crawl4ai_llm_link_rank"
                    item["score"] = int(item.get("score", 0) or 0) + max(boost, 20)
                    if dept:
                        item["llm_department"] = dept
            ranked.append(item)
        return sorted(ranked, key=lambda row: -int(row.get("score", 0) or 0))


def extract_links(school_name: str, html: str, source_url: str, depth: int, same_domain_only: bool) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()
    source_teacher_context = any(token in source_url.lower() for token in ["teacher", "faculty", "people", "szdw", "jsdw", "/sz/", "/jzyg/"])
    for a in soup.find_all("a"):
        title = clean(a.get_text(" ", strip=True))
        href = a.get("href") or ""
        url = norm_url(href, source_url)
        if not title or not url.startswith(("http://", "https://")) or url.lower().endswith((".jpg", ".png", ".gif", ".zip", ".rar")):
            continue
        if same_domain_only and not same_or_sub_domain(source_url, url):
            continue
        score, hits = keyword_score(title, url)
        if source_teacher_context and re.fullmatch(r"[A-Z]|\d+|首页|上一页|下一页|尾页", title, flags=re.I):
            score = max(score, 8)
            hits = hits + ["teacher_pagination"]
        if score <= 0:
            continue
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "school_name": school_name,
            "source_url": source_url,
            "depth": depth,
            "title": title,
            "url": url,
            "score": score,
            "matched_keywords": ";".join(hits),
            "discovery_method": "crawl4ai_link_keyword" if hits else "link_keyword",
        })
    rows.sort(key=lambda r: -int(r["score"]))
    return rows


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def is_valid_person_name(name: str) -> bool:
    name = clean(name)
    if not NAME_RE.match(name):
        return False
    if name in NAV_NAME_STOPWORDS:
        return False
    if any(token in name for token in ["登录", "密码", "研究", "课程", "学院", "中心", "通知", "招生", "办公", "指南", "讲座", "教育", "寄语", "项目", "奖励", "致辞", "说明", "学者", "沿革", "学会", "协会", "我们", "信息", "职位", "过程", "变化", "物理", "新闻", "动态", "通道", "概况", "队伍", "介绍", "热点", "要闻", "友情链接", "北工大", "数说", "邮箱", "专栏", "专题", "职称", "网关"]):
        return False
    if name.endswith(("系", "团队", "人才", "名师", "门户")):
        return False
    if name.startswith(("中国", "北京", "全国")):
        return False
    return True


def valid_title(text: str) -> str:
    text = clean(text)
    if len(text) > 40:
        return ""
    if text and re.search(r"教授|副教授|讲师|研究员|院士|导师|博士后|助理教授|长聘|预聘|工程师|主任|副主任", text):
        return text
    return ""




def clean_research_field(value: str) -> str:
    value = clean(value).strip("：:;；,，、")
    if not value or value in {"无", "暂无", "暂无内容"}:
        return ""
    if len(value) > 260:
        return ""
    return value

def extract_labeled_field(text: str, labels: list[str]) -> str:
    for label in labels:
        m = re.search(label + r"\s*[:：]?\s*([^\n；;|]+)", text)
        if m:
            return clean(m.group(1))
    return ""


def extract_labeled_line_value(text: str, labels: list[str], max_len: int = 180) -> str:
    lines = [clean(line) for line in text.splitlines() if clean(line)]
    for idx, line in enumerate(lines):
        for label in labels:
            compact_line = re.sub(r"\s+", "", line)
            compact_label = re.sub(r"\s+", "", label)
            if label not in line and compact_label not in compact_line:
                continue
            value = ""
            m = re.search(re.escape(label) + r"\s*[:：]\s*(.*)", line)
            if m:
                value = clean(m.group(1))
            if not value:
                compact_match = re.search(re.escape(compact_label) + r"[:：]?(.*)", compact_line)
                if compact_match:
                    value = clean(compact_match.group(1))
            if not value and idx + 1 < len(lines):
                value = clean(lines[idx + 1])
            value = re.split(
                r"\s+(?:教育经历|工作经历|代表性|论文|招生|获奖|个人简介|联系方式|办公地点|联系电话|研究方向|研究领域)[:：]?",
                value,
                maxsplit=1,
            )[0]
            bad_values = {"论文成果", "暂无内容", "社会兼职", "【查看更多】", "简历", "教育与工作经历", "更多", "MORE +"}
            if value and value not in NAV_NAME_STOPWORDS and value not in bad_values:
                return value[:max_len].strip()
    return ""


def name_from_profile_title(title: str) -> str:
    title = clean(title)
    for sep in ["-", "中文主页", "--", "|", "_"]:
        if sep in title:
            title = title.split(sep, 1)[0]
            break
    parts = title.split()
    return clean(parts[0]) if parts else clean(title)


def first_valid_heading_name(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = clean(tag.get_text(" ", strip=True))
        if is_valid_person_name(text):
            return text
    return ""


def infer_department_from_profile_text(text: str) -> str:
    patterns = [
        r"北京交通大学\s*([\u4e00-\u9fa5]{2,24}(?:学院|学系|研究院|研究所|中心|学部|系))",
        r"工作经历.*?([\u4e00-\u9fa5]{2,24}(?:学院|学系|研究院|研究所|中心|学部|系))",
        r"通讯地址[:：]?\s*北京交通大学([\u4e00-\u9fa5]{2,24}(?:学院|学系|研究院|研究所|中心|学部|系))",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.S)
        if not m:
            continue
        dept = clean(m.group(1))
        if is_valid_department_name(dept) or re.search(r"(学院|学系|研究院|研究所|中心|学部|系)$", dept):
            return dept
    return ""


def extract_teacher_profile_detail(school_name: str, soup: BeautifulSoup, source_url: str, default_department: str = "") -> list[dict]:
    title_text = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    text = "\n".join(clean(x) for x in soup.get_text("\n").splitlines() if clean(x))
    name = extract_labeled_line_value(text, ["姓名"], 20) or first_valid_heading_name(soup) or name_from_profile_title(title_text)
    if not is_valid_person_name(name):
        return []
    title = valid_title(extract_labeled_line_value(text, ["职称", "职 称", "职位", "职务"], 50))
    if not title:
        m = TITLE_RE.search(text[:1500])
        title = valid_title(clean(m.group(1))) if m else ""
    if not title and re.search(r"博士生导师|硕士生导师|研究生导师", text[:2000]):
        title = clean(re.search(r"(博士生导师|硕士生导师|研究生导师)", text[:2000]).group(1))
    research = clean_research_field(
        extract_labeled_line_value(text, ["专业及研究领域"], 220)
        or extract_labeled_line_value(text, ["研究领域"], 220)
        or extract_labeled_line_value(text, ["研究方向"], 220)
    )
    if not research:
        m = re.search(r"从事([^。\n]{6,180})", text)
        if m:
            research = clean_research_field("从事" + clean(m.group(1)))
    unit = extract_labeled_line_value(text, ["所在单位", "所属单位", "工作单位", "院系", "学院"], 80)
    inferred_department = infer_department_from_profile_text(text)
    if inferred_department and (not unit or not re.search(r"(学院|学系|研究院|研究所|中心|学部|系)$", unit)):
        unit = inferred_department
    email_match = EMAIL_RE.search(text)
    if not (title or research or email_match or unit):
        return []
    return [
        {
            "school_name": school_name,
            "department": default_department if default_department != school_name else inferred_department,
            "teacher_name": name,
            "title": title,
            "research_fields": research,
            "teacher_unit": unit,
            "email": clean(email_match.group(0)) if email_match else "",
            "teacher_profile_url": source_url,
            "source_url": source_url,
            "confidence": 0.84 if title or research else 0.72,
            "extract_method": "rule_teacher_profile_detail",
        }
    ]


def is_probable_teacher_profile_url(url: str) -> bool:
    lower = url.lower()
    return any(
        token in lower
        for token in [
            "faculty.",
            "/teacher/",
            "/teachers/",
            "/people/",
            "/jszy/",
            "/szdw/",
            "/jzyg/",
            "/info/",
            "/professor",
        ]
    )


def discover_teacher_profile_links(html: str, source_url: str, depth: int, limit: int = 80) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()
    for a in soup.find_all("a"):
        title = clean(a.get_text(" ", strip=True))
        href = a.get("href") or ""
        if not is_valid_person_name(title) or not href:
            continue
        url = norm_url(href, source_url)
        if not url.startswith(("http://", "https://")) or not is_probable_teacher_profile_url(url):
            continue
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "school_name": "",
                "source_url": source_url,
                "depth": depth,
                "title": title,
                "url": url,
                "score": 42,
                "matched_keywords": "姓名链接;教师详情URL",
                "discovery_method": "teacher_profile_name_link",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def extract_inline_teacher_anchors(school_name: str, soup: BeautifulSoup, source_url: str, default_department: str = "") -> list[dict]:
    rows = []
    for a in soup.find_all("a"):
        text = clean(a.get_text(" ", strip=True))
        href = a.get("href") or ""
        if len(text) < 12 or len(text) > 500:
            continue
        if not any(label in text for label in ["所属系别", "研究方向", "研究领域", "Email", "邮箱"]):
            continue
        before_unit = re.split(r"所属系别[:：]", text, maxsplit=1)
        head = before_unit[0]
        rest = before_unit[1] if len(before_unit) == 2 else text
        parts = head.split()
        if not parts:
            continue
        name = clean(parts[0])
        if not is_valid_person_name(name):
            continue
        title_match = TITLE_RE.search(head)
        title = valid_title(title_match.group(1)) if title_match else ""
        unit = ""
        if len(before_unit) == 2:
            unit = clean(re.split(r"(?:Tel|电话|Email|邮箱|研究方向|研究领域)[:：]", rest, maxsplit=1)[0])
            if len(unit) > 50:
                unit = ""
        research = clean_research_field(extract_labeled_field(text, ["研究方向", "研究领域"]))
        email_match = EMAIL_RE.search(text)
        if not (title or research or email_match):
            continue
        rows.append(
            {
                "school_name": school_name,
                "department": default_department,
                "teacher_name": name,
                "title": title,
                "research_fields": research,
                "teacher_unit": unit,
                "email": clean(email_match.group(0)) if email_match else "",
                "teacher_profile_url": norm_url(href, source_url),
                "source_url": source_url,
                "confidence": 0.88,
                "extract_method": "rule_teacher_inline_anchor",
            }
        )
    return rows


def semantic_tokens(text: str) -> set[str]:
    text = clean(text)
    if not text:
        return set()
    raw_parts = re.split(r"[，,、;；/\s（）()]+", text)
    stop = {
        "研究方向", "不区分研究方向", "方向", "研究", "理论", "应用", "方法", "问题",
        "基础", "相关", "交叉", "及其", "中的", "与", "和", "的",
    }
    tokens = set()
    for part in raw_parts:
        part = clean(part)
        if len(part) < 2 or part in stop:
            continue
        tokens.add(part)
    return tokens


def build_teacher_program_links(school_name: str, teachers: list[dict], program_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    links = []
    unified = []
    seen = set()
    programs_by_dept: dict[str, list[dict]] = {}
    for program in program_rows:
        dept = clean(program.get("department", ""))
        if not dept:
            continue
        programs_by_dept.setdefault(dept, []).append(program)
    for teacher in teachers:
        dept = clean(teacher.get("department", ""))
        teacher_tokens = semantic_tokens(teacher.get("research_fields", ""))
        if not dept or not teacher_tokens:
            continue
        for program in programs_by_dept.get(dept, []):
            major = clean(program.get("major_name", ""))
            direction = clean(program.get("research_direction", ""))
            # Keep this as a weak but reviewable relation: match teacher research fields
            # to catalog directions, not broad major names such as "计算数学".
            overlap = teacher_tokens & semantic_tokens(direction)
            if not overlap:
                continue
            key = (dept, major, direction, teacher.get("teacher_name", ""))
            if key in seen:
                continue
            seen.add(key)
            match_type = "research_keyword_overlap:" + ";".join(sorted(overlap))
            confidence = 0.55
            links.append({
                "school_name": school_name,
                "department": dept,
                "major_name": major,
                "research_direction": direction,
                "teacher_name": teacher.get("teacher_name", ""),
                "match_type": match_type,
                "confidence": confidence,
                "source_url": teacher.get("source_url", ""),
            })
            unified.append({
                "school_name": school_name,
                "teacher_name": teacher.get("teacher_name", ""),
                "department": dept,
                "level": clean(program.get("level", "")),
                "major_name": major,
                "research_direction": direction,
                "teacher_title": teacher.get("title", ""),
                "teacher_research_fields": teacher.get("research_fields", ""),
                "teacher_unit": teacher.get("teacher_unit", ""),
                "teacher_profile_url": teacher.get("teacher_profile_url", ""),
                "match_type": match_type,
                "source_url": teacher.get("source_url", ""),
            })
    return links, unified


def extract_departments(school_name: str, html: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for a in soup.find_all("a"):
        name = clean(a.get_text(" ", strip=True))
        href = a.get("href") or ""
        if not href:
            continue
        if name in {"学部与院系", "标识系统", "其它实体研究机构"}:
            continue
        if not is_valid_department_name(name):
            continue
        rows.append({
            "school_name": school_name,
            "department": name,
            "division": "",
            "site_url": norm_url(href, source_url),
            "source_url": source_url,
            "confidence": 0.65,
            "extract_method": "rule_department_anchor",
        })
    dedup = {}
    for row in rows:
        dedup[(row["department"], row["site_url"])] = row
    return list(dedup.values())


def extract_math_style_teachers(school_name: str, soup: BeautifulSoup, source_url: str, default_department: str) -> list[dict]:
    rows = []
    for left in soup.select("div.left"):
        info = left.select_one("div.left_info")
        right = left.find_next_sibling("div", class_="right")
        if not info or not right:
            continue
        a = info.select_one("h3 a")
        name = clean(a.get_text(" ", strip=True)) if a else ""
        if not is_valid_person_name(name):
            continue
        fields = {}
        for p in right.find_all("p"):
            key = clean((p.find("strong") or {}).get_text(" ", strip=True) if p.find("strong") else "").rstrip("：:")
            value = clean((p.find("i") or p).get_text(" ", strip=True))
            if key:
                fields[key] = value
        title = valid_title(fields.get("职 称") or fields.get("职称") or "")
        research = clean_research_field(fields.get("研究方向", ""))
        unit = clean(fields.get("系", ""))
        office = clean(info.select_one("p.addr").get_text(" ", strip=True)) if info.select_one("p.addr") else ""
        email = clean(info.select_one("p.mail").get_text(" ", strip=True)) if info.select_one("p.mail") else ""
        if not (title or research or email):
            continue
        rows.append({
            "school_name": school_name,
            "department": default_department,
            "teacher_name": name,
            "title": title,
            "research_fields": research[:160],
            "teacher_unit": unit[:40],
            "email": email,
            "teacher_profile_url": norm_url(a.get("href") or "", source_url) if a else "",
            "source_url": source_url,
            "confidence": 0.92,
            "extract_method": "rule_teacher_math_card",
        })
    return rows


def extract_teachers(school_name: str, html: str, source_url: str, default_department: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = extract_teacher_profile_detail(school_name, soup, source_url, default_department)
    rows.extend(extract_math_style_teachers(school_name, soup, source_url, default_department))
    rows.extend(extract_inline_teacher_anchors(school_name, soup, source_url, default_department))
    # Pattern A: label-based cards.
    for node in soup.select("li, div, td"):
        text = node.get_text("\n", strip=True)
        compact = clean(text)
        if len(compact) < 8 or len(compact) > 650:
            continue
        if any(bad in compact for bad in ["版权所有", "当前位置", "首页", "上一页", "下一页", "快速导航"]):
            continue
        if re.search(r"所属\s*系\s*别", compact) and re.search(r"研究\s*方\s*向", compact):
            continue
        lines = [clean(x) for x in text.splitlines() if clean(x)]
        name = ""
        profile = ""
        for a in node.find_all("a"):
            t = clean(a.get_text(" ", strip=True))
            if is_valid_person_name(t):
                name = t
                profile = norm_url(a.get("href") or "", source_url)
                break
        if not name and lines and is_valid_person_name(lines[0]):
            name = lines[0]
        if not name or not is_valid_person_name(name):
            continue
        title = valid_title(extract_labeled_field(text, ["职称", "职  称", "职位", "职务"]))
        research = clean_research_field(extract_labeled_field(text, ["研究方向", "研究领域", "研究兴趣"]))
        unit = extract_labeled_field(text, ["研究所", "系", "单位", "课题组"])
        email_match = EMAIL_RE.search(compact)
        if len(research) > 160:
            research = ""
        if len(unit) > 40:
            unit = ""
        has_field_evidence = bool(title or research or email_match)
        # High precision for batch runs: a short Chinese anchor plus a link is often just navigation.
        # Keep teacher candidates only when the same block contains field evidence.
        if not has_field_evidence:
            continue
        if research and not (title or email_match):
            continue
        if email_match and not (title or research) and (not profile or profile.startswith("javascript")):
            continue
        confidence = 0.55
        if title or research or email_match:
            confidence = 0.78
        if profile and profile != source_url and has_field_evidence:
            confidence = 0.86
        rows.append({
            "school_name": school_name,
            "department": default_department,
            "teacher_name": name,
            "title": title,
            "research_fields": research,
            "teacher_unit": unit,
            "email": clean(email_match.group(0)) if email_match else "",
            "teacher_profile_url": profile,
            "source_url": source_url,
            "confidence": confidence,
            "extract_method": "rule_teacher_block",
        })
    dedup = {}
    for row in rows:
        key = (row["teacher_name"], row.get("teacher_profile_url") or row["source_url"])
        old = dedup.get(key)
        if not old or float(row["confidence"]) > float(old["confidence"]):
            dedup[key] = row
    return list(dedup.values())


def maybe_department_from_title(title: str) -> str:
    m = re.search(r"([\u4e00-\u9fa5]{2,20}(?:学院|学系|研究院|研究所|中心|学部|系))", title)
    return m.group(1) if m else ""


def is_department_context(title: str, url: str, text: str, default_department: str, school_name: str) -> bool:
    if default_department and default_department != school_name:
        return True
    hay = f"{title} {url} {text[:1200]}"
    return any(k in hay for k in ["学院", "学系", "研究院", "研究所", "学部", "系所"])




def department_name_weight(name: str) -> int:
    name = clean(name)
    if not name:
        return 0
    if "学院" in name or "学部" in name:
        return 50
    if "学系" in name or name.endswith("系"):
        return 25
    if "研究院" in name or "研究所" in name:
        return 20
    if "中心" in name:
        return 10
    return 1


def register_department_host(mapping: dict[str, tuple[str, int]], site_url: str, department: str) -> None:
    host = urlparse(clean(site_url)).netloc.lower().removeprefix("www.")
    department = clean(department)
    if not host or not department:
        return
    weight = department_name_weight(department)
    old = mapping.get(host)
    if old is None or weight > old[1]:
        mapping[host] = (department, weight)


def mapped_department_for_url(mapping: dict[str, tuple[str, int]], url: str) -> str:
    host = urlparse(clean(url)).netloc.lower().removeprefix("www.")
    if not host:
        return ""
    if host in mapping:
        return mapping[host][0]
    for known_host, value in mapping.items():
        if host.endswith("." + known_host) or known_host.endswith("." + host):
            return value[0]
    return ""


def is_noise_admission_or_news_link(text: str) -> bool:
    text = clean(text).lower()
    return any(token in text for token in ["复试", "拟录取", "调剂", "名单", "通知", "公告", "新闻", "动态", "公示"])


def link_frontier_priority(link: dict, department: str, school_name: str) -> int:
    link_text = (clean(link.get("title")) + " " + clean(link.get("url"))).lower()
    priority = int(link.get("score", 0) or 0)
    is_noise = is_noise_admission_or_news_link(link_text)
    if any(token in link_text for token in ["师资", "教师", "导师", "faculty", "teacher", "people", "/szdw/", "/szll/", "/jsml/", "/zzjs/"]) and not is_noise:
        priority += 100
    if is_noise:
        priority -= 180
    if department and department != school_name:
        priority += 10
    return priority

def read_search_seed_links(path: str, official_site: str = "", limit: int = 80) -> list[dict]:
    if not path:
        return []
    csv_path = Path(path)
    if not csv_path.exists():
        return []
    official_host = urlparse(official_site).netloc.lower().removeprefix("www.") if official_site else ""
    rows = []
    try:
        df = pd.read_csv(csv_path).fillna("")
    except Exception:
        return []
    for _, row in df.iterrows():
        url = clean(row.get("url", ""))
        if not url.startswith(("http://", "https://")):
            continue
        if str(row.get("is_official", "")).strip() in {"0", "false", "False"}:
            continue
        if official_host:
            h = urlparse(url).netloc.lower().removeprefix("www.")
            # Keep subdomains such as ues.pku.edu.cn for pku.edu.cn.
            if not (h == official_host or h.endswith("." + official_host)):
                continue
        score = int(float(row.get("score", 0) or 0))
        if score <= 0:
            continue
        dept = clean(row.get("department", "")) or maybe_department_from_title(clean(row.get("title", "")))
        rows.append(
            {
                "url": url,
                "depth": 0,
                "default_department": dept,
                "priority": score + 120,
                "source": "search_discovered_seed",
            }
        )
    dedup = {}
    for row in rows:
        dedup[row["url"]] = row
    return sorted(dedup.values(), key=lambda item: -int(item["priority"]))[:limit]


def run_pipeline(args: argparse.Namespace) -> None:
    out_dir = Path(args.output_dir)
    pages_dir = out_dir / "pages"
    markdown_dir = out_dir / "pages_markdown"
    pages_dir.mkdir(parents=True, exist_ok=True)
    markdown_dir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(args.engine, args.sleep)
    classifier = PageClassifier(args.enable_ai, args.model)

    seed_urls = [args.site] + list(args.extra_site or [])
    frontier = [
        {
            "url": url,
            "depth": 0,
            "default_department": args.school,
            "priority": 100,
            "source": "official_seed" if i == 0 else "manual_debug_seed",
        }
        for i, url in enumerate(seed_urls)
    ]
    frontier.extend(read_search_seed_links(args.search_links_csv, official_site=args.site, limit=args.search_seed_limit))
    visited = set()
    candidates = []
    classifications = []
    departments = []
    department_host_map: dict[str, tuple[str, int]] = {}
    teachers = []
    programs = []
    issues = []
    visited_by_host: dict[str, int] = {}

    while frontier and len(visited) < args.max_pages:
        frontier.sort(key=lambda item: (-int(item["priority"]), int(item["depth"]), item["url"]))
        selected_index = 0
        if args.max_pages_per_host > 0:
            for idx, item in enumerate(frontier):
                host = urlparse(clean(item.get("url", ""))).netloc.lower().removeprefix("www.")
                if int(item.get("depth", 0)) == 0 or visited_by_host.get(host, 0) < args.max_pages_per_host:
                    selected_index = idx
                    break
        current = frontier.pop(selected_index)
        url = current["url"]
        depth = int(current["depth"])
        default_department = current["default_department"]
        if url in visited:
            continue
        visited.add(url)
        host = urlparse(url).netloc.lower().removeprefix("www.")
        visited_by_host[host] = visited_by_host.get(host, 0) + 1
        try:
            fetched = fetcher.fetch(url)
            html = fetched.get("html", "")
            raw_markdown = fetched.get("raw_markdown", "")
            fit_markdown = fetched.get("fit_markdown", "")
            text = fit_markdown or raw_markdown or page_text(html)
            title = clean(BeautifulSoup(html, "html.parser").title.get_text(" ", strip=True)) if BeautifulSoup(html, "html.parser").title else ""
            page_path = pages_dir / f"{len(visited):04d}.html"
            page_path.write_text(html, encoding="utf-8")
            if raw_markdown:
                (markdown_dir / f"{len(visited):04d}.raw.md").write_text(raw_markdown, encoding="utf-8")
            if fit_markdown:
                (markdown_dir / f"{len(visited):04d}.fit.md").write_text(fit_markdown, encoding="utf-8")
            cls = classifier.classify(title, url, text)
            classifications.append({"school_name": args.school, "url": url, "title": title, **cls})

            if (
                cls["page_type"] == "department"
                or depth == 0
                or "department" in url.lower()
                or "学部与院系" in text[:1000]
                or "院系设置" in text[:1000]
            ):
                discovered = extract_departments(args.school, html, url)
                departments.extend(discovered)
                for dept_row in discovered:
                    register_department_host(department_host_map, dept_row.get("site_url", ""), dept_row.get("department", ""))
                for dept_row in discovered:
                    dept_url = clean(dept_row.get("site_url", ""))
                    dept_name = clean(dept_row.get("department", ""))
                    if not dept_url.startswith(("http://", "https://")) or dept_url in visited:
                        continue
                    if not (same_or_sub_domain(args.site, dept_url) or (args.allow_external and related_official_url(args.site, dept_url))):
                        continue
                    frontier.append(
                        {
                            "url": dept_url,
                            "depth": 0,
                            "default_department": dept_name,
                            "priority": 95,
                            "source": "department_site_seed",
                        }
                    )
            if cls["page_type"] in {"teacher_list", "teacher_profile"}:
                dept = mapped_department_for_url(department_host_map, url)
                if not dept:
                    dept = default_department if default_department != args.school else maybe_department_from_title(title)
                teachers.extend(extract_teachers(args.school, html, url, dept))

            max_depth_for_page = args.max_depth
            if is_department_context(title, url, text, default_department, args.school):
                max_depth_for_page += args.department_extra_depth
            if depth < max_depth_for_page:
                links = extract_links(args.school, html, url, depth + 1, same_domain_only=not args.allow_external)
                links = classifier.ai_rank_links(title, url, text, links)
                if cls["page_type"] == "teacher_list" or any(token in text[:2000] for token in ["在职教职工", "师资", "教师队伍", "教职员工"]):
                    profile_links = discover_teacher_profile_links(html, url, depth + 1, limit=args.profile_links_per_page)
                    for profile_link in profile_links:
                        profile_link["school_name"] = args.school
                    links = profile_links + links
                candidates.extend(links)
                teacher_links = [
                    link for link in links
                    if any(k.lower() in (link["title"] + " " + link["url"]).lower() for k in TEACHER_KEYWORDS)
                    and not is_noise_admission_or_news_link(link["title"] + " " + link["url"])
                ]
                profile_links = [link for link in links if link.get("discovery_method") == "teacher_profile_name_link"]
                ranked_links = sorted(links, key=lambda item: -link_frontier_priority(item, clean(item.get("llm_department")) or maybe_department_from_title(item.get("title", "")) or default_department, args.school))
                selected_links = []
                seen_selected = set()
                for link in profile_links + sorted(teacher_links, key=lambda item: -link_frontier_priority(item, clean(item.get("llm_department")) or maybe_department_from_title(item.get("title", "")) or default_department, args.school)) + ranked_links:
                    if link["url"] in seen_selected:
                        continue
                    selected_links.append(link)
                    seen_selected.add(link["url"])
                    if len(selected_links) >= args.links_per_page:
                        break
                for link in selected_links:
                    if is_document_url(link["url"]):
                        continue
                    dept = mapped_department_for_url(department_host_map, link["url"]) or clean(link.get("llm_department")) or maybe_department_from_title(link["title"]) or default_department
                    if link["url"] not in visited:
                        priority = link_frontier_priority(link, dept, args.school)
                        frontier.append(
                            {
                                "url": link["url"],
                                "depth": depth + 1,
                                "default_department": dept,
                                "priority": priority,
                                "source": "discovered_link",
                            }
                        )
        except Exception as e:
            issues.append({"school_name": args.school, "url": url, "stage": "fetch_or_parse", "issue_type": type(e).__name__, "message": str(e)[:300]})

    # Dedupe outputs.
    candidates = list({(r["title"], r["url"]): r for r in candidates}.values())
    departments = list({(r["department"], r["site_url"]): r for r in departments}.values())
    teachers = list({(r["teacher_name"], r["teacher_profile_url"] or r["source_url"]): r for r in teachers}.values())

    save_csv(out_dir / "candidate_pages.csv", candidates, CANDIDATE_FIELDS)
    save_csv(out_dir / "page_classification.csv", classifications, CLASSIFICATION_FIELDS)
    save_csv(out_dir / "discovered_departments.csv", departments, DEPARTMENT_FIELDS)
    save_csv(out_dir / "departments.csv", departments, DEPARTMENT_FIELDS)
    save_csv(out_dir / "programs.csv", programs, PROGRAM_FIELDS)
    save_csv(out_dir / "teachers.csv", teachers, TEACHER_FIELDS)
    save_csv(out_dir / "program_teacher_links.csv", [], PROGRAM_TEACHER_LINK_FIELDS)
    save_csv(out_dir / "extraction_issues.csv", issues, ISSUE_FIELDS)

    # Compatibility names for existing hierarchy tool.
    save_csv(out_dir / "unified_programs.csv", [], ["school_name", "level", "catalog_type", "department", "major_code", "major_name", "degree_type", "direction_code", "study_mode", "research_direction", "teacher_name", "teacher_profile_url", "enrollment_plan", "exam_subjects", "admissions_note", "source_url"])
    save_csv(out_dir / "unified_teachers.csv", [], UNIFIED_TEACHER_FIELDS)

    adapter_results = []
    if should_run_pdf_adapter(candidates, classifications):
        try:
            adapter_results.append(run_pdf_adapter(args.school, out_dir, refresh=False, max_pages=args.pdf_max_pages))
            parsed_pdf_programs = out_dir / "generic_pdf_programs.csv"
            if parsed_pdf_programs.exists():
                pdf_programs = pd.read_csv(parsed_pdf_programs).fillna("")
                if not pdf_programs.empty:
                    save_csv(out_dir / "unified_programs.csv", pdf_programs.to_dict("records"), [
                        "school_name", "level", "catalog_type", "department", "major_code", "major_name",
                        "degree_type", "direction_code", "study_mode", "research_direction", "teacher_name",
                        "teacher_profile_url", "enrollment_plan", "exam_subjects", "admissions_note", "source_url",
                    ])
                    program_projection = []
                    for _, row in pdf_programs.iterrows():
                        program_projection.append(
                            {
                                "school_name": row.get("school_name", ""),
                                "department": row.get("department", ""),
                                "level": row.get("level", ""),
                                "major_code": row.get("major_code", ""),
                                "major_name": row.get("major_name", ""),
                                "research_direction": row.get("research_direction", ""),
                                "source_url": row.get("source_url", ""),
                                "confidence": 0.85,
                                "extract_method": "generic_admission_pdf",
                            }
                        )
                    save_csv(out_dir / "programs.csv", program_projection, PROGRAM_FIELDS)
                    teacher_program_links, unified_teacher_links = build_teacher_program_links(args.school, teachers, pdf_programs.to_dict("records"))
                    save_csv(out_dir / "program_teacher_links.csv", teacher_program_links, PROGRAM_TEACHER_LINK_FIELDS)
                    save_csv(out_dir / "unified_teachers.csv", unified_teacher_links, UNIFIED_TEACHER_FIELDS)
        except Exception as e:
            issues.append(
                {
                    "school_name": args.school,
                    "url": str(out_dir / "candidate_pages.csv"),
                    "stage": "adapter:generic_admission_pdf",
                    "issue_type": type(e).__name__,
                    "message": str(e)[:300],
                }
            )
            save_csv(out_dir / "extraction_issues.csv", issues, ISSUE_FIELDS)
    (out_dir / "adapter_results.json").write_text(json.dumps(adapter_results, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = [
        f"# {args.school} 智能抽取流水线结果", "",
        f"- 抓取页面：{len(visited)}",
        f"- 候选页面：{len(candidates)}",
        f"- 页面分类：{len(classifications)}",
        f"- 院系候选：{len(departments)}",
        f"- 教师候选：{len(teachers)}",
        f"- 触发适配器：{len(adapter_results)}",
        f"- 问题记录：{len(issues)}",
        "", "## 说明",
        "- 这是通用 MVP，适合批量学校先跑候选发现和页面分类。",
        "- AI 分类开启后，如果接口失败会自动降级规则分类。",
        "- 最终可信数据需要结合学校专门目录源或高置信度页面继续校验。",
    ]
    (out_dir / "pipeline_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(out_dir), "pages": len(visited), "candidates": len(candidates), "departments": len(departments), "teachers": len(teachers), "issues": len(issues)}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="通用高校智能信息抽取流水线：crawl4ai + AI分类 + 规则校验")
    parser.add_argument("--school", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--extra-site", action="append", default=[], help="调试兜底：额外种子入口；批量生产默认不使用")
    parser.add_argument("--search-links-csv", default="", help="Tavily/搜索发现的官方入口 CSV，字段至少包含 url，可选 score/department/is_official")
    parser.add_argument("--search-seed-limit", type=int, default=80)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--engine", choices=["crawl4ai", "requests"], default="crawl4ai")
    parser.add_argument("--enable-ai", action="store_true")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--links-per-page", type=int, default=12)
    parser.add_argument("--profile-links-per-page", type=int, default=80)
    parser.add_argument("--max-pages-per-host", type=int, default=35, help="单个学院/子域名最多访问页数，避免一个学院吃完整体预算；0 表示不限制")
    parser.add_argument("--department-extra-depth", type=int, default=1)
    parser.add_argument("--pdf-max-pages", type=int, default=120)
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.1)
    args = parser.parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
