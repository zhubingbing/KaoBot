#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup


DEPARTMENT_FIELDS = ["school_name", "department", "division", "site_url", "source_url", "confidence", "extract_method"]
TEACHER_FIELDS = [
    "school_name", "department", "teacher_name", "title", "research_fields",
    "teacher_unit", "email", "teacher_profile_url", "source_url", "confidence", "extract_method",
]
UNIFIED_TEACHER_FIELDS = [
    "school_name", "teacher_name", "department", "level", "major_name", "research_direction",
    "teacher_title", "teacher_research_fields", "teacher_unit", "teacher_profile_url", "match_type", "source_url",
]
LINK_FIELDS = ["school_name", "department", "teacher_list_url", "total_teachers_hint", "total_pages_hint", "source_url"]


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def save_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def fetch(session: requests.Session, url: str, sleep: float) -> str:
    resp = session.get(url, timeout=25, allow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    if sleep:
        time.sleep(sleep)
    return resp.text


def valid_department(name: str) -> bool:
    name = clean(name)
    if not name or name in {"机关及其他单位"}:
        return False
    return bool(re.search(r"(学院|研究院|体育部|中心|学部|系)$", name))


def parse_department_links(html: str, portal_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if "CollegeTeacherList" not in href:
            continue
        department = clean(a.get("title") or a.get_text(" ", strip=True))
        if not valid_department(department):
            continue
        url = urljoin(portal_url, href)
        key = (department, url)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"department": department, "teacher_list_url": url})
    return rows


def total_pages_from_html(html: str) -> tuple[str, int]:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    m = re.search(r"共\s*(\d+)\s*条\s*\d+\s*/\s*(\d+)", text)
    if m:
        return m.group(1), max(1, int(m.group(2)))
    m = re.search(r"totalpage=(\d+)", html)
    if m:
        return "", max(1, int(m.group(1)))
    return "", 1


def page_url(url: str, page: int) -> str:
    base, _ = urldefrag(url)
    parsed = urlparse(base)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if page > 1:
        qs["PAGENUM"] = [str(page)]
    query = urlencode(qs, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))


def parse_teachers(html: str, school: str, department: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()
    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        profile = urljoin(source_url, a["href"])
        if "shi.buaa.edu.cn" not in profile:
            continue
        text = clean(a.get_text(" ", strip=True))
        parts = text.split()
        if not parts or not re.fullmatch(r"[\u4e00-\u9fa5·]{2,6}", parts[0]):
            continue
        teacher_name = parts[0]
        title = clean(" ".join(parts[1:]))
        key = (teacher_name, profile)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "school_name": school,
            "department": department,
            "teacher_name": teacher_name,
            "title": title,
            "research_fields": "",
            "teacher_unit": department,
            "email": "",
            "teacher_profile_url": profile,
            "source_url": source_url,
            "confidence": "0.92",
            "extract_method": "faculty_portal_tsites_list",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="解析 tsites 教师主页门户：学院列表 -> 学院教师分页列表")
    parser.add_argument("--school", required=True)
    parser.add_argument("--portal-url", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-departments", type=int, default=0)
    parser.add_argument("--max-pages-per-department", type=int, default=2, help="0 表示抓全量分页")
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "Mozilla/5.0 school-intelligence-pipeline/0.1"})

    portal_html = fetch(session, args.portal_url, args.sleep)
    department_links = parse_department_links(portal_html, args.portal_url)
    if args.max_departments > 0:
        department_links = department_links[:args.max_departments]

    teacher_rows = []
    link_rows = []
    department_rows = []
    for item in department_links:
        department = item["department"]
        first_url = item["teacher_list_url"]
        first_html = fetch(session, first_url, args.sleep)
        total_teachers, total_pages = total_pages_from_html(first_html)
        pages_to_fetch = total_pages if args.max_pages_per_department == 0 else min(total_pages, args.max_pages_per_department)
        department_rows.append({
            "school_name": args.school,
            "department": department,
            "division": "",
            "site_url": first_url,
            "source_url": args.portal_url,
            "confidence": "0.95",
            "extract_method": "faculty_portal_tsites_college",
        })
        link_rows.append({
            "school_name": args.school,
            "department": department,
            "teacher_list_url": first_url,
            "total_teachers_hint": total_teachers,
            "total_pages_hint": total_pages,
            "source_url": args.portal_url,
        })
        teacher_rows.extend(parse_teachers(first_html, args.school, department, first_url))
        for page in range(2, pages_to_fetch + 1):
            current_url = page_url(first_url, page)
            current_html = fetch(session, current_url, args.sleep)
            teacher_rows.extend(parse_teachers(current_html, args.school, department, current_url))

    dedup = {}
    for row in teacher_rows:
        dedup[(row["department"], row["teacher_name"], row["teacher_profile_url"])] = row
    teacher_rows = list(dedup.values())

    save_csv(out_dir / "departments.csv", department_rows, DEPARTMENT_FIELDS)
    save_csv(out_dir / "teachers.csv", teacher_rows, TEACHER_FIELDS)
    save_csv(out_dir / "unified_teachers.csv", [], UNIFIED_TEACHER_FIELDS)
    save_csv(out_dir / "program_teacher_links.csv", [], ["school_name", "department", "major_name", "research_direction", "teacher_name", "match_type", "confidence", "source_url"])
    save_csv(out_dir / "faculty_portal_teacher_links.csv", link_rows, LINK_FIELDS)
    summary = [
        f"# {args.school} 教师门户解析结果",
        "",
        f"- 院系入口：{len(department_rows)}",
        f"- 教师记录：{len(teacher_rows)}",
        f"- 每院系分页上限：{args.max_pages_per_department if args.max_pages_per_department else '全量'}",
        f"- 来源：{args.portal_url}",
    ]
    (out_dir / "faculty_portal_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print({"departments": len(department_rows), "teachers": len(teacher_rows), "output_dir": str(out_dir)})


if __name__ == "__main__":
    main()
