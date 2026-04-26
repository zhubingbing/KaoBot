#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import re
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

FIELDS = [
    "school_name",
    "level",
    "catalog_type",
    "department",
    "major_code",
    "major_name",
    "degree_type",
    "direction_code",
    "study_mode",
    "research_direction",
    "teacher_name",
    "teacher_profile_url",
    "enrollment_plan",
    "exam_subjects",
    "admissions_note",
    "source_url",
]


def clean(value) -> str:
    value = "" if pd.isna(value) else str(value)
    value = value.replace("\u3000", " ")
    return re.sub(r"\s+", " ", value).strip()


def split_code_name(value: str) -> tuple[str, str]:
    value = clean(value)
    m = re.match(r"^([A-Za-z0-9]+)\s*(.+)$", value)
    if not m:
        return "", value
    return clean(m.group(1)), clean(m.group(2))


def split_direction(value: str) -> tuple[str, str]:
    value = clean(value)
    m = re.match(r"^([A-Za-z0-9]+)\s*(.+)$", value)
    if not m:
        return "", value
    return clean(m.group(1)), clean(m.group(2))


def session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": "Mozilla/5.0 admission-html-table-parser/0.1"})
    return s


def fetch(s: requests.Session, url: str) -> str:
    resp = s.get(url, timeout=40, allow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def load_page_cache(output_dir: Path) -> dict[str, str]:
    classification = output_dir / "page_classification.csv"
    pages_dir = output_dir / "pages"
    if not classification.exists() or not pages_dir.exists():
        return {}
    cache = {}
    try:
        df = pd.read_csv(classification).fillna("")
    except Exception:
        return {}
    for idx, row in df.iterrows():
        url = clean(row.get("url", ""))
        page_path = pages_dir / f"{idx + 1:04d}.html"
        if url and page_path.exists():
            try:
                cache[url] = page_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                cache[url] = page_path.read_text(encoding="utf-8", errors="ignore")
    return cache


def parse_master_table(school_name: str, html: str, source_url: str) -> list[dict]:
    rows = []
    try:
        dfs = pd.read_html(StringIO(html))
    except ValueError:
        return rows
    if not dfs:
        return rows
    df = dfs[0].fillna("")
    if df.empty:
        return rows
    header = [clean(x) for x in df.iloc[0].tolist()]
    if "院系所" not in header or "专业" not in header or "研究方向" not in header:
        return rows
    for _, row in df.iloc[1:].iterrows():
        values = [clean(x) for x in row.tolist()]
        if len(values) < 7:
            continue
        dept_code, dept = split_code_name(values[0])
        major_code, major = split_code_name(values[1])
        direction_code, direction = split_direction(values[2])
        if not dept or not major:
            continue
        rows.append(
            {
                "school_name": school_name,
                "level": "硕士",
                "catalog_type": "generic_html_master",
                "department": dept,
                "major_code": major_code,
                "major_name": major,
                "degree_type": "",
                "direction_code": direction_code,
                "study_mode": values[3],
                "research_direction": direction,
                "teacher_name": "",
                "teacher_profile_url": "",
                "enrollment_plan": "",
                "exam_subjects": values[4],
                "admissions_note": values[6],
                "source_url": source_url,
            }
        )
    return rows


def parse_phd_table(school_name: str, html: str, source_url: str) -> list[dict]:
    rows = []
    try:
        dfs = pd.read_html(StringIO(html))
    except ValueError:
        return rows
    if not dfs:
        return rows
    df = dfs[0].fillna("")
    if len(df) < 4 or len(df.columns) < 10:
        return rows
    for _, row in df.iloc[3:].iterrows():
        values = [clean(x) for x in row.tolist()]
        if len(values) < 10:
            continue
        dept_code, dept = values[0], values[1]
        major_code, major = values[2], values[3]
        direction_code, direction = values[4], values[5]
        if not dept or not major or dept in {"学院", "名称"}:
            continue
        exam = "；".join(x for x in [f"外国语:{values[6]}", f"加试1:{values[7]}", f"加试2:{values[8]}"] if clean(x.split(":", 1)[1]))
        rows.append(
            {
                "school_name": school_name,
                "level": "博士",
                "catalog_type": "generic_html_phd",
                "department": dept,
                "major_code": major_code,
                "major_name": major,
                "degree_type": "",
                "direction_code": direction_code,
                "study_mode": "",
                "research_direction": direction,
                "teacher_name": "",
                "teacher_profile_url": "",
                "enrollment_plan": "",
                "exam_subjects": exam,
                "admissions_note": values[9],
                "source_url": source_url,
            }
        )
    return rows


def infer_level_from_url_title(source_url: str, html: str) -> str:
    title = ""
    soup = BeautifulSoup(html, "html.parser")
    if soup.title:
        title = clean(soup.title.get_text(" ", strip=True))
    hay = f"{title} {source_url}".lower()
    if "博士" in hay or "phd" in hay or "bszs" in hay:
        return "博士"
    return "硕士"


def parse_plain_text_catalog(school_name: str, html: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [clean(line) for line in text.splitlines() if clean(line)]
    level = infer_level_from_url_title(source_url, html)
    rows = []
    dept = ""
    major_code = ""
    major_name = ""
    direction_code = ""
    direction = ""
    direction_re = re.compile(r"^[_＿]?([0-9]{2})\s*(.+)$")
    dept_re = re.compile(r"^([0-9]{3})\s+(.{2,40}(?:学院|学部|学系|研究院|研究所|中心|系|部))$")
    major_re = re.compile(r"^([A-Za-z]?[0-9]{4,6}[A-Za-z0-9]*)\s+(.+?)(?:[（(]拟招生人数[:：]?[0-9]+人?[）)])?$")
    stop = {"网站首页", "招生目录", "正文", "当前位置：", "备注：", "学院、招生学科代码、学科名称及研究方向"}

    def emit() -> None:
        if dept and major_code and major_name and direction:
            rows.append({
                "school_name": school_name,
                "level": level,
                "catalog_type": "generic_html_plain_text",
                "department": dept,
                "major_code": major_code,
                "major_name": major_name,
                "degree_type": "",
                "direction_code": direction_code,
                "study_mode": "",
                "research_direction": direction,
                "teacher_name": "",
                "teacher_profile_url": "",
                "enrollment_plan": "",
                "exam_subjects": "",
                "admissions_note": "",
                "source_url": source_url,
            })

    pending_dept_code = ""
    pending_major_code = ""
    for line in lines:
        if line in stop or len(line) > 120:
            continue
        if re.fullmatch(r"[0-9]{3}", line):
            pending_dept_code = line
            continue
        if pending_dept_code and re.search(r"(学院|学部|学系|研究院|研究所|中心|系|部)$", line):
            emit()
            dept = line
            pending_dept_code = ""
            major_code = major_name = direction_code = direction = ""
            continue
        pending_dept_code = ""
        if re.fullmatch(r"[A-Za-z]?[0-9]{4,6}[A-Za-z0-9]*", line):
            pending_major_code = line
            continue
        if pending_major_code and not direction_re.match(line):
            if not any(bad in line for bad in ["招生", "考试", "目录", "网站", "报名", "人数", "科目", "备注", "学制", "学费"]):
                emit()
                major_code = pending_major_code
                major_name = line
                direction_code = direction = ""
                pending_major_code = ""
                continue
        pending_major_code = ""
        dm = dept_re.match(line)
        if dm:
            emit()
            dept = clean(dm.group(2))
            major_code = major_name = direction_code = direction = ""
            continue
        mm = major_re.match(line)
        if mm and not direction_re.match(line):
            name = clean(mm.group(2))
            if any(bad in name for bad in ["招生", "考试", "目录", "网站", "报名"]):
                continue
            emit()
            major_code = clean(mm.group(1))
            major_name = name
            direction_code = direction = ""
            continue
        rm = direction_re.match(line)
        if rm and major_code:
            emit()
            direction_code = clean(rm.group(1))
            direction = clean(rm.group(2))
            continue
    emit()

    dedup = {}
    for row in rows:
        key = (row["level"], row["department"], row["major_code"], row["major_name"], row["direction_code"], row["research_direction"], row["source_url"])
        dedup[key] = row
    return list(dedup.values())


def discover_ruc_phd_urls(html: str, source_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        text = clean(a.get_text(" ", strip=True))
        if re.search(r"^\d{3}.+(学院|研究院|系|书院)", text) and "bszyml" in href:
            urls.append(urljoin(source_url, href))
    return list(dict.fromkeys(urls))


def save_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def fetch_or_empty(s: requests.Session, url: str, issues: list[dict], stage: str, page_cache: dict[str, str]) -> str:
    if url in page_cache:
        return page_cache[url]
    try:
        return fetch(s, url)
    except Exception as exc:
        issues.append({"stage": stage, "url": url, "error": f"{type(exc).__name__}: {exc}"})
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="通用 HTML 招生专业目录表格解析器")
    parser.add_argument("--school-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--master-url", action="append", default=[])
    parser.add_argument("--phd-index-url", action="append", default=[])
    parser.add_argument("--phd-url", action="append", default=[])
    args = parser.parse_args()

    s = session()
    rows = []
    issues = []
    out = Path(args.output_dir)
    page_cache = load_page_cache(out)
    for url in args.master_url:
        html = fetch_or_empty(s, url, issues, "master_fetch", page_cache)
        if html:
            parsed = parse_master_table(args.school_name, html, url)
            rows.extend(parsed or parse_plain_text_catalog(args.school_name, html, url))
    phd_urls = list(args.phd_url or [])
    for url in args.phd_index_url:
        html = fetch_or_empty(s, url, issues, "phd_index_fetch", page_cache)
        if html:
            phd_urls.extend(discover_ruc_phd_urls(html, url))
    for url in list(dict.fromkeys(phd_urls)):
        html = fetch_or_empty(s, url, issues, "phd_fetch", page_cache)
        if html:
            parsed = parse_phd_table(args.school_name, html, url)
            rows.extend(parsed or parse_plain_text_catalog(args.school_name, html, url))
    if rows:
        save_csv(out / "generic_html_programs.csv", rows)
        save_csv(out / "unified_programs.csv", rows)
        save_csv(out / "curated_graduate_programs.csv", rows)
    if issues:
        with (out / "generic_html_program_issues.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["stage", "url", "error"])
            writer.writeheader()
            writer.writerows(issues)
    print(f"html_programs {len(rows)}")


if __name__ == "__main__":
    main()
