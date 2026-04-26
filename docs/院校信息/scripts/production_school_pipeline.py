#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
One production entrypoint for school -> department -> program markdown.

Default policy:
- Crawl4AI is the only crawl engine used by the main discovery pipeline.
- Tavily is disabled.
- Teachers are not required for the main pass.
- Existing PDF/HTML catalog adapters are invoked internally when candidate
  official admission catalog pages are found.
"""

import argparse
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slug(name: str) -> str:
    mapping = {"北京大学": "pku", "清华大学": "tsinghua", "中国人民大学": "ruc"}
    return mapping.get(name, re.sub(r"\W+", "_", name).strip("_") or "school")


def load_school(input_csv: Path, school_name: str) -> dict:
    df = pd.read_csv(input_csv).fillna("")
    row = df[df["school_name"].astype(str).str.strip().eq(school_name)]
    if row.empty:
        raise SystemExit(f"未在 {input_csv} 找到学校：{school_name}")
    return row.iloc[0].to_dict()


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path).fillna("")


def discover_html_catalog_urls(output_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """Return (master_urls, phd_index_urls, phd_urls) from pipeline candidates."""
    frames = []
    for name in ["candidate_pages.csv", "page_classification.csv"]:
        df = read_csv(output_dir / name)
        if not df.empty:
            frames.append(df)
    if not frames:
        return [], [], []

    rows = []
    for df in frames:
        for _, row in df.iterrows():
            rows.append({
                "title": clean(row.get("title", "")),
                "url": clean(row.get("url", "")),
            })

    master_urls = []
    phd_index_urls = []
    phd_urls = []
    for row in rows:
        title = row["title"]
        url = row["url"]
        if not url.startswith(("http://", "https://")):
            continue
        hay = f"{title} {url}"
        if ".pdf" in url.lower():
            continue
        if re.search(r"20\d{2}.*硕士.*(专业目录|招生目录|招生专业|学科专业目录|学科目录)", hay):
            master_urls.append(url)
        if re.search(r"20\d{2}.*(博士|学博|专博).*(专业目录|招生目录|招生专业|学科专业目录|学科目录)", hay):
            phd_index_urls.append(url)
        if re.search(r"/20\d{2}/.*bszyml/.+\.htm", url):
            phd_urls.append(url)
    return list(dict.fromkeys(master_urls)), list(dict.fromkeys(phd_index_urls)), list(dict.fromkeys(phd_urls))


def ensure_empty_teacher_files(output_dir: Path) -> None:
    files = {
        "teachers.csv": [
            "school_name", "department", "teacher_name", "title", "research_fields",
            "teacher_unit", "email", "teacher_profile_url", "source_url", "confidence", "extract_method",
        ],
        "unified_teachers.csv": [
            "school_name", "teacher_name", "department", "level", "major_name", "research_direction",
            "teacher_title", "teacher_research_fields", "teacher_unit", "teacher_profile_url",
            "match_type", "source_url",
        ],
    }
    for filename, fields in files.items():
        path = output_dir / filename
        if path.exists():
            continue
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()


def count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(pd.read_csv(path).fillna(""))
    except Exception:
        return 0


def restore_programs_if_empty(output_dir: Path) -> None:
    unified = output_dir / "unified_programs.csv"
    if count_rows(unified) > 0:
        return
    for fallback_name in ["curated_graduate_programs.csv", "generic_html_programs.csv", "generic_pdf_programs.csv"]:
        fallback = output_dir / fallback_name
        if count_rows(fallback) > 0:
            shutil.copyfile(fallback, unified)
            print(f"恢复专业表：{fallback} -> {unified}")
            return


def main() -> None:
    parser = argparse.ArgumentParser(description="生产入口：Crawl4AI 官网发现 -> 专业目录/院系教师池解析 -> departments 生成")
    parser.add_argument("--school", required=True)
    parser.add_argument("--input", default="院校信息.csv")
    parser.add_argument("--output-root", default="output/school_finals")
    parser.add_argument("--departments-root", default="departments")
    parser.add_argument("--max-pages", type=int, default=120)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--links-per-page", type=int, default=30)
    parser.add_argument("--profile-links-per-page", type=int, default=80, help="师资列表页中跟进教师详情链接的上限")
    parser.add_argument("--allow-external", action="store_true", help="允许发现官方跨子域入口，如 pgs.ruc.edu.cn")
    parser.add_argument("--enable-ai", action="store_true", help="启用 LLM 页面/链接分类；无 OPENAI_API_KEY 时自动退回规则")
    parser.add_argument("--extra-site", action="append", default=[], help="官方补充入口；不是 Tavily")
    parser.add_argument("--skip-crawl", action="store_true", help="已有 candidate_pages 时跳过 Crawl4AI 抓取")
    args = parser.parse_args()

    row = load_school(Path(args.input), args.school)
    site = clean(row.get("official_site_url", ""))
    if not site:
        raise SystemExit(f"{args.school} 缺少 official_site_url")

    output_dir = Path(args.output_root) / f"{slug(args.school)}_final"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_crawl:
        crawl_cmd = [
            sys.executable,
            "pipeline_internal/school_intelligence_pipeline.py",
            "--school", args.school,
            "--site", site,
            "--output-dir", str(output_dir),
            "--engine", "crawl4ai",
            "--max-pages", str(args.max_pages),
            "--max-depth", str(args.max_depth),
            "--links-per-page", str(args.links_per_page),
            "--profile-links-per-page", str(args.profile_links_per_page),
        ]
        for extra in args.extra_site:
            crawl_cmd.extend(["--extra-site", extra])
        if args.allow_external:
            crawl_cmd.append("--allow-external")
        if args.enable_ai:
            crawl_cmd.append("--enable-ai")
        run(crawl_cmd)

    master_urls, phd_index_urls, phd_urls = discover_html_catalog_urls(output_dir)
    if master_urls or phd_index_urls or phd_urls:
        html_cmd = [
            sys.executable,
            "pipeline_internal/parse_admission_html_tables.py",
            "--school-name", args.school,
            "--output-dir", str(output_dir),
        ]
        for url in master_urls:
            html_cmd.extend(["--master-url", url])
        for url in phd_index_urls:
            html_cmd.extend(["--phd-index-url", url])
        for url in phd_urls:
            html_cmd.extend(["--phd-url", url])
        run(html_cmd)

    # PDF adapter is already attempted by school_intelligence_pipeline. If HTML
    # produced programs, it overwrites unified_programs.csv with the same schema.
    restore_programs_if_empty(output_dir)
    ensure_empty_teacher_files(output_dir)

    build_cmd = [
        sys.executable,
        "pipeline_internal/build_department_markdown_tree.py",
        "--school", args.school, str(output_dir),
        "--root", args.departments_root,
        "--max-programs", "200",
        "--max-teachers", "200",
        "--max-linked", "200",
    ]
    run(build_cmd)

    print(
        f"完成：school={args.school} output={output_dir} "
        f"programs={count_rows(output_dir / 'unified_programs.csv')} "
        f"teachers={count_rows(output_dir / 'teachers.csv')} "
        f"linked_teachers={count_rows(output_dir / 'unified_teachers.csv')} "
        f"departments={Path(args.departments_root) / args.school}"
    )


if __name__ == "__main__":
    main()
