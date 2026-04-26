#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

PROGRAM_FIELDS = [
    "school_name", "level", "catalog_type", "department", "major_code", "major_name", "degree_type",
    "direction_code", "study_mode", "research_direction", "teacher_name", "teacher_profile_url",
    "enrollment_plan", "exam_subjects", "admissions_note", "source_url",
]
PDF_LINK_FIELDS = ["school_name", "department", "level_hint", "title", "pdf_url", "source_page"]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\u3000", " ")).strip()


def split_direction(value: str) -> tuple[str, str]:
    value = clean(value)
    m = re.match(r"^[_＿]?([A-Za-z0-9]+)[.、\s]*(.+)$", value)
    if not m:
        return "", value
    return clean(m.group(1)), clean(m.group(2))


def session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": "Mozilla/5.0 generic-admission-pdf-parser/0.1"})
    return s


def fetch(s: requests.Session, url: str) -> bytes:
    resp = s.get(url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return resp.content


def save_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_candidate_urls(path: Path) -> list[str]:
    if not path.exists():
        return []
    df = pd.read_csv(path).fillna("")
    urls = []
    for _, row in df.iterrows():
        title = clean(row.get("title", ""))
        url = clean(row.get("url", ""))
        hay = f"{title} {url}"
        if not url.startswith(("http://", "https://")):
            continue
        if any(k in hay for k in ["招生", "专业", "目录", "硕士", "博士", "研究生", "admission", "zsxx", "zyml"]):
            urls.append(url)
    return list(dict.fromkeys(urls))


def infer_level(text: str) -> str:
    lowered = text.lower()
    if "博士" in text or "bszs" in lowered or "_bs_" in lowered or "phd" in lowered:
        return "博士"
    if "硕士" in text or "sszs" in lowered or "_ss_" in lowered or "master" in lowered:
        return "硕士"
    return ""


def year_allowed(text: str, target_year: str) -> bool:
    if not target_year:
        return True
    years = set(re.findall(r"20\d{2}", text))
    return not years or target_year in years


def catalog_priority(text: str) -> int:
    lowered = text.lower()
    score = 0
    for token, value in [
        ("zyml", 40),
        ("专业目录", 40),
        ("招生目录", 35),
        ("/yx/", 30),
        ("zsml_", 30),
        ("硕士招生", 20),
        ("博士招生", 20),
        ("sszs", 15),
        ("bszs", 15),
        ("研究生", 8),
        ("招生", 5),
    ]:
        if token in lowered or token in text:
            score += value
    if "/docs/" in lowered and ".pdf" in lowered:
        score -= 20
    return score


def discover_pdf_links(s: requests.Session, seed_urls: list[str], max_pages: int = 80, target_year: str = "") -> list[dict]:
    rows = []
    seen = set()
    queue = sorted(list(dict.fromkeys(seed_urls)), key=lambda u: -catalog_priority(u))
    visited = set()
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            html = fetch(s, url).decode("utf-8", errors="ignore")
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a"):
            title = clean(a.get_text(" ", strip=True))
            href = a.get("href") or ""
            link = urljoin(url, href)
            hay = f"{title} {link}"
            if link.lower().endswith((".pdf", ".xlsx", ".xls")):
                if not year_allowed(hay, target_year):
                    continue
                key = link
                if key in seen:
                    continue
                seen.add(key)
                # Many official catalog indexes use anchor text as department name.
                department = title if 2 <= len(title) <= 40 else ""
                rows.append({
                    "school_name": "",
                    "department": department,
                    "level_hint": infer_level(hay),
                    "title": title,
                    "pdf_url": link,
                    "source_page": url,
                })
            elif any(k in hay for k in ["专业目录", "招生目录", "zyml", "硕士", "博士"]):
                if link.startswith(("http://", "https://")) and link not in visited:
                    if catalog_priority(hay) >= 40:
                        queue.insert(0, link)
                    elif len(queue) + len(visited) < max_pages:
                        queue.append(link)
        time.sleep(0.05)
    return rows


def pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    chunks = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    text = "\n".join(chunks).replace("\u3000", " ")
    return re.sub(r"[ \t]+", " ", text)


def logical_lines(text: str) -> list[str]:
    return [clean(x) for x in text.splitlines() if clean(x)]


def split_blocks(lines: list[str], header_re) -> list[list[str]]:
    blocks = []
    cur = []
    for line in lines:
        if header_re.match(line):
            if cur:
                blocks.append(cur)
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def parse_master_like(text: str, school_name: str, department: str, source_url: str, catalog_type: str) -> list[dict]:
    lines = logical_lines(text)
    header_re = re.compile(r"^([A-Z]?\d{6})\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(.*))?$")
    rows = []
    for block in split_blocks(lines, header_re):
        m = header_re.match(block[0])
        if not m:
            continue
        major_code, major_name, full_plan, part_plan, full_reco, part_reco, rest = m.groups()
        blob = clean(" ".join(([rest] if rest else []) + block[1:]))
        dir_matches = list(re.finditer(r"(\d{2})\.\s*(.*?)(全日制|非全日制)", blob)) or [None]
        exam_subjects = ""
        note = ""
        exam_start = re.search(r"①\s*", blob)
        if exam_start:
            exam_part = blob[exam_start.start():]
            note_match = re.search(r"考试科目|专业备注|备注", exam_part)
            if note_match and note_match.start() > 0:
                exam_subjects = clean(exam_part[: note_match.start()])
                note = clean(exam_part[note_match.start():])
            else:
                exam_subjects = clean(exam_part)
        for dm in dir_matches:
            rows.append({
                "school_name": school_name,
                "level": "硕士",
                "catalog_type": catalog_type,
                "department": department,
                "major_code": clean(major_code),
                "major_name": clean(major_name),
                "degree_type": "",
                "direction_code": dm.group(1) if dm else "",
                "study_mode": dm.group(3) if dm else "",
                "research_direction": clean(dm.group(2)) if dm else "",
                "teacher_name": "",
                "teacher_profile_url": "",
                "enrollment_plan": f"全日制{full_plan}; 非全日制{part_plan}; 拟推免全日制{full_reco}; 拟推免非全日制{part_reco}",
                "exam_subjects": exam_subjects,
                "admissions_note": note,
                "source_url": source_url,
            })
    return rows


def parse_phd_like(text: str, school_name: str, department: str, source_url: str, catalog_type: str) -> list[dict]:
    lines = logical_lines(text)
    rows = []
    current_code = ""
    current_name = ""
    major_re = re.compile(r"^(\d{6})\s+(.+)$")
    dir_with_mode = re.compile(r"^(\d{2})\.\s*(.*?)(全日制|非全日制)(?:\s+(.*))?$")
    dir_without_mode = re.compile(r"^(\d{2})\.\s*(.*)$")
    pending = None
    for line in lines:
        if line.startswith(("招生院系", "计划招生数", "其中拟接收", "备注说明", "专业代码", department)):
            continue
        mm = major_re.match(line)
        if mm and not dir_with_mode.match(line) and not dir_without_mode.match(line):
            if pending:
                rows.append(pending)
                pending = None
            current_code = mm.group(1)
            current_name = clean(mm.group(2))
            continue
        dm = dir_with_mode.match(line) or dir_without_mode.match(line)
        if dm and current_code:
            if pending:
                rows.append(pending)
            has_mode = len(dm.groups()) >= 4
            pending = {
                "school_name": school_name,
                "level": "博士",
                "catalog_type": catalog_type,
                "department": department,
                "major_code": current_code,
                "major_name": current_name,
                "degree_type": "",
                "direction_code": dm.group(1),
                "study_mode": dm.group(3) if has_mode else "",
                "research_direction": clean(dm.group(2)),
                "teacher_name": "",
                "teacher_profile_url": "",
                "enrollment_plan": "",
                "exam_subjects": "",
                "admissions_note": clean(dm.group(4) if has_mode else ""),
                "source_url": source_url,
            }
            continue
        if pending and not pending.get("study_mode"):
            mode = re.search(r"(全日制|非全日制)$", line)
            if mode:
                extra = clean(line[: mode.start()])
                if extra:
                    pending["research_direction"] = clean(pending["research_direction"] + " " + extra)
                pending["study_mode"] = mode.group(1)
            else:
                pending["research_direction"] = clean(pending["research_direction"] + " " + line)
    if pending:
        rows.append(pending)
    return rows


def parse_excel_catalog(path: Path, link: dict, school_name: str) -> list[dict]:
    rows = []
    try:
        sheets = pd.read_excel(path, sheet_name=None, header=None).values()
    except Exception:
        return rows
    department = ""
    level = link.get("level_hint") or infer_level(link.get("pdf_url", "") + link.get("title", "")) or "硕士"
    for df in sheets:
        df = df.fillna("")
        headers = []
        header_idx = -1
        for idx, row in df.iterrows():
            values = [clean(x) for x in row.tolist()]
            joined = " ".join(values)
            dept_match = re.search(r"学院名称[:：]\s*([^\s]+)", joined)
            if dept_match:
                department = clean(dept_match.group(1))
            if any("专业代码" == x for x in values) and any("专业名称" == x for x in values):
                headers = values
                header_idx = idx
                break
        if header_idx < 0:
            continue
        col = {name: i for i, name in enumerate(headers) if name}
        cur_major_code = ""
        cur_major_name = ""
        cur_exam = []
        for _, row in df.iloc[header_idx + 1 :].iterrows():
            values = [clean(x) for x in row.tolist()]
            joined = " ".join(values)
            dept_match = re.search(r"学院名称[:：]\s*([^\s]+)", joined)
            if dept_match:
                department = clean(dept_match.group(1))
                cur_major_code = cur_major_name = ""
                cur_exam = []
                continue
            major_code = values[col.get("专业代码", -1)] if "专业代码" in col else ""
            major_name = values[col.get("专业名称", -1)] if "专业名称" in col else ""
            direction = values[col.get("研究方向", -1)] if "研究方向" in col else ""
            study_mode = values[col.get("学习方式", -1)] if "学习方式" in col else ""
            exam = values[col.get("初试科目", -1)] if "初试科目" in col else ""
            retest = values[col.get("复试科目", -1)] if "复试科目" in col else ""
            note = values[col.get("备注", -1)] if "备注" in col else ""
            if major_code:
                cur_major_code = major_code
            if major_name:
                cur_major_name = major_name
            if exam:
                cur_exam.append(exam)
            if not direction or not cur_major_code or not cur_major_name:
                continue
            d_code, d_name = split_direction(direction)
            rows.append({
                "school_name": school_name,
                "level": level,
                "catalog_type": "generic_excel_catalog",
                "department": department,
                "major_code": cur_major_code,
                "major_name": cur_major_name,
                "degree_type": "",
                "direction_code": d_code,
                "study_mode": study_mode,
                "research_direction": d_name,
                "teacher_name": "",
                "teacher_profile_url": "",
                "enrollment_plan": "",
                "exam_subjects": "；".join(cur_exam[-4:]),
                "admissions_note": clean("；".join(x for x in [retest, note] if x)),
                "source_url": link["pdf_url"],
            })
    return rows


def parse_pdf(path: Path, link: dict, school_name: str) -> list[dict]:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return parse_excel_catalog(path, link, school_name)
    text = pdf_text(path)
    level = link.get("level_hint") or infer_level(link.get("pdf_url", "") + link.get("title", ""))
    catalog_type = "generic_pdf_master" if level == "硕士" else ("generic_pdf_phd" if level == "博士" else "generic_pdf")
    department = clean(link.get("department"))
    if level == "博士":
        rows = parse_phd_like(text, school_name, department, link["pdf_url"], catalog_type)
    elif level == "硕士":
        rows = parse_master_like(text, school_name, department, link["pdf_url"], catalog_type)
    else:
        rows = parse_master_like(text, school_name, department, link["pdf_url"], catalog_type)
        if not rows:
            rows = parse_phd_like(text, school_name, department, link["pdf_url"], catalog_type)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="通用招生专业目录 PDF 发现与解析器")
    parser.add_argument("--school-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--candidate-pages", default="")
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--target-year", default=str(datetime.now().year))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    out = Path(args.output_dir)
    pdf_dir = out / "admission_pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    s = session()
    seeds = list(args.seed_url)
    if args.candidate_pages:
        seeds.extend(read_candidate_urls(Path(args.candidate_pages)))
    seeds = list(dict.fromkeys(seeds))
    links = discover_pdf_links(s, seeds, max_pages=args.max_pages, target_year=args.target_year)
    for row in links:
        row["school_name"] = args.school_name
    save_csv(out / "admission_pdf_links.csv", links, PDF_LINK_FIELDS)

    program_rows = []
    for idx, link in enumerate(links, start=1):
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(link["pdf_url"]).name or f"pdf_{idx}.pdf")
        pdf_path = pdf_dir / f"{idx:03d}_{safe_name}"
        if args.refresh or not pdf_path.exists():
            try:
                pdf_path.write_bytes(fetch(s, link["pdf_url"]))
                time.sleep(0.05)
            except Exception:
                continue
        try:
            program_rows.extend(parse_pdf(pdf_path, link, args.school_name))
        except Exception:
            continue
    # de-duplicate conservatively.
    dedup = {}
    for row in program_rows:
        key = (row["level"], row["department"], row["major_code"], row["major_name"], row["direction_code"], row["research_direction"], row["source_url"])
        dedup[key] = row
    program_rows = list(dedup.values())
    save_csv(out / "generic_pdf_programs.csv", program_rows, PROGRAM_FIELDS)
    print(f"PDF links {len(links)}; program rows {len(program_rows)}")


if __name__ == "__main__":
    main()
