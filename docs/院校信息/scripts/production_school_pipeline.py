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
import os
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


def load_source_config(config_csv: Path, school_name: str) -> dict:
    if not config_csv.exists():
        return {}
    df = pd.read_csv(config_csv).fillna("")
    row = df[df["school_name"].astype(str).str.strip().eq(school_name)]
    if row.empty:
        return {}
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


def backup_current_outputs(output_dir: Path) -> dict[str, str]:
    backups = {}
    for filename in [
        "candidate_pages.csv",
        "page_classification.csv",
        "departments.csv",
        "teachers.csv",
        "unified_teachers.csv",
        "program_teacher_links.csv",
        "unified_programs.csv",
    ]:
        path = output_dir / filename
        if path.exists():
            backups[filename] = path.read_text(encoding="utf-8-sig")
    return backups


def restore_backups(output_dir: Path, backups: dict[str, str]) -> None:
    for filename, content in backups.items():
        (output_dir / filename).write_text(content, encoding="utf-8-sig")


def detect_entry_type(entry_url: str, configured_type: str) -> str:
    configured_type = clean(configured_type)
    if configured_type and configured_type != "auto":
        return configured_type
    host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", clean(entry_url)).split("/", 1)[0].lower())
    if host == "faculty.buaa.edu.cn":
        return "faculty_portal_tsites"
    if any(token in clean(entry_url).lower() for token in ["yxsz", "department", "college", "faculty"]):
        return "department_index"
    return "seed_url"


def run_configured_entry(args: argparse.Namespace, config: dict, output_dir: Path) -> bool:
    entry_url = clean(config.get("entry_url"))
    if not entry_url:
        return False
    entry_type = detect_entry_type(entry_url, clean(config.get("entry_type")))
    if entry_type == "faculty_portal_tsites":
        run([
            sys.executable,
            "pipeline_internal/parse_faculty_portal_tsites.py",
            "--school", args.school,
            "--portal-url", entry_url,
            "--output-dir", str(output_dir),
            "--max-pages-per-department", str(int(float(config.get("max_teacher_pages_per_department") or 2))),
        ])
        return clean(config.get("crawl_mode")) == "config_only"
    if entry_type == "department_index":
        run([
            sys.executable,
            "pipeline_internal/parse_department_index_crawl4ai.py",
            "--school", args.school,
            "--entry-url", entry_url,
            "--output-dir", str(output_dir),
        ])
        return clean(config.get("crawl_mode")) == "config_only"
    if entry_type == "seed_url":
        args.extra_site.append(entry_url)
        return False
    raise SystemExit(f"不支持的 entry_type：{entry_type} ({entry_url})")


def department_file_count(root: Path, school: str) -> int:
    school_dir = root / school
    if not school_dir.exists():
        return 0
    return len([p for p in school_dir.glob("*.md") if p.name != "README.md"])


def has_effective_output(output_dir: Path, departments_root: Path, school: str) -> bool:
    programs = count_rows(output_dir / "unified_programs.csv")
    teachers = count_rows(output_dir / "teachers.csv")
    linked_teachers = count_rows(output_dir / "unified_teachers.csv")
    department_files = department_file_count(departments_root, school)
    if programs > 0:
        return True
    if teachers >= 5 or linked_teachers >= 5:
        return True
    return department_files >= 3 and (teachers > 0 or linked_teachers > 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="生产入口：Crawl4AI 官网发现 -> 专业目录/院系教师池解析 -> departments 生成")
    parser.add_argument("--school", required=True)
    parser.add_argument("--input", default="院校信息.csv")
    parser.add_argument("--source-config", default="configs/school_pipeline_sources.csv")
    parser.add_argument("--department-overrides", default="configs/department_overrides.csv")
    parser.add_argument("--output-root", default="output/school_finals")
    parser.add_argument("--departments-root", default="departments")
    parser.add_argument("--max-pages", type=int, default=120)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--links-per-page", type=int, default=30)
    parser.add_argument("--crawler-engine", choices=["crawl4ai", "crawl4ai_docker"], default=os.getenv("SCHOOL_PIPELINE_CRAWLER_ENGINE", "crawl4ai"), help="crawl4ai 使用方式：本机或 Docker 服务")
    parser.add_argument("--profile-links-per-page", type=int, default=80, help="师资列表页中跟进教师详情链接的上限")
    parser.add_argument("--teacher-pages-per-department", type=int, default=2, help="每个院系官网最多分析几个师资候选页")
    parser.add_argument("--teacher-workers", type=int, default=4, help="院系教师发现并发 worker 数")
    parser.add_argument("--max-teacher-departments", type=int, default=0, help="最多分析多少个院系官网；0 表示不限制")
    parser.add_argument("--skip-department-teachers", action="store_true", help="跳过院系官网师资页发现")
    parser.add_argument("--max-pages-per-host", type=int, default=35, help="单个学院/子域名最多访问页数，避免一个学院吃完整体预算；0 表示不限制")
    parser.add_argument("--allow-external", action="store_true", help="允许发现官方跨子域入口，如 pgs.ruc.edu.cn")
    parser.add_argument("--enable-ai", action="store_true", help="启用 LLM 页面/链接分类；无 OPENAI_API_KEY 时自动退回规则")
    parser.add_argument("--extra-site", action="append", default=[], help="官方补充入口；不是 Tavily")
    parser.add_argument("--skip-crawl", action="store_true", help="已有 candidate_pages 时跳过 Crawl4AI 抓取")
    parser.add_argument("--force", action="store_true", help="即使已有有效输出也重新抓取")
    args = parser.parse_args()
    if args.crawler_engine == "crawl4ai_docker":
        os.environ["SCHOOL_PIPELINE_CRAWLER_ENGINE"] = "crawl4ai_docker"

    row = load_school(Path(args.input), args.school)
    config = load_source_config(Path(args.source_config), args.school)
    site = clean(config.get("official_site_url")) or clean(row.get("official_site_url", ""))
    if not site:
        raise SystemExit(f"{args.school} 缺少 official_site_url")

    output_dir = Path(args.output_root) / f"{slug(args.school)}_final"
    output_dir.mkdir(parents=True, exist_ok=True)
    departments_root = Path(args.departments_root)

    if not args.force and has_effective_output(output_dir, departments_root, args.school):
        print(
            f"跳过：{args.school} 已有有效输出；如需重跑请加 --force。"
            f" output={output_dir} departments={departments_root / args.school}"
        )
        return

    backups = backup_current_outputs(output_dir)
    config_only_done = run_configured_entry(args, config, output_dir)
    if not args.skip_crawl and not config_only_done:
        crawl_cmd = [
            sys.executable,
            "pipeline_internal/school_intelligence_pipeline.py",
            "--school", args.school,
            "--site", site,
            "--output-dir", str(output_dir),
            "--engine", args.crawler_engine,
            "--max-pages", str(args.max_pages),
            "--max-depth", str(args.max_depth),
            "--links-per-page", str(args.links_per_page),
            "--profile-links-per-page", str(args.profile_links_per_page),
            "--max-pages-per-host", str(args.max_pages_per_host),
        ]
        for extra in args.extra_site:
            crawl_cmd.extend(["--extra-site", extra])
        if args.allow_external:
            crawl_cmd.append("--allow-external")
        if args.enable_ai:
            crawl_cmd.append("--enable-ai")
        run(crawl_cmd)
        if count_rows(output_dir / "candidate_pages.csv") == 0 and count_rows(output_dir / "extraction_issues.csv") > 0 and backups:
            restore_backups(output_dir, backups)
            print("抓取失败，已恢复本次运行前的有效输出。")

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
    if not args.skip_department_teachers and count_rows(output_dir / "departments.csv") > 0:
        teacher_cmd = [
            sys.executable,
            "pipeline_internal/discover_department_teachers.py",
            "--school", args.school,
            "--output-dir", str(output_dir),
            "--teacher-pages-per-department", str(args.teacher_pages_per_department),
            "--max-departments", str(args.max_teacher_departments),
            "--workers", str(args.teacher_workers),
            "--overrides", args.department_overrides,
        ]
        if args.enable_ai:
            teacher_cmd.append("--enable-ai")
        run(teacher_cmd)

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
