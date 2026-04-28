#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Batch wrapper for production_school_pipeline.py.

Default behavior is conservative: skip schools that already have effective
outputs. Use --force to rerun.
"""

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from production_school_pipeline import count_rows, has_effective_output, slug


def main() -> None:
    parser = argparse.ArgumentParser(description="批量跑院校信息生产流水线；默认跳过已有结果")
    parser.add_argument("--input", default="院校信息.csv")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--school", action="append", default=[], help="只跑指定学校；可重复传")
    parser.add_argument("--output-root", default="output/school_finals")
    parser.add_argument("--departments-root", default="departments")
    parser.add_argument("--summary", default="output/school_finals/batch_summary.csv")
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--links-per-page", type=int, default=18)
    parser.add_argument("--profile-links-per-page", type=int, default=30)
    parser.add_argument("--max-pages-per-host", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=420, help="单所学校最长运行秒数")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--enable-ai", action="store_true")
    parser.add_argument("--force", action="store_true", help="已有有效输出也重跑")
    args = parser.parse_args()

    df = pd.read_csv(args.input).fillna("")
    if args.school:
        schools = [name for name in args.school if name in set(df["school_name"].astype(str))]
    else:
        schools = df["school_name"].astype(str).tolist()[args.offset:args.offset + args.limit]

    output_root = Path(args.output_root)
    departments_root = Path(args.departments_root)
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []

    for index, school in enumerate(schools, start=1):
        output_dir = output_root / f"{slug(school)}_final"
        start = time.time()
        status = "ok"
        message = ""

        if not args.force and has_effective_output(output_dir, departments_root, school):
            status = "skipped"
            message = "已有有效输出；如需重跑请加 --force"
            print(f"===== [{index}/{len(schools)}] {school}: skipped =====", flush=True)
        else:
            print(f"===== [{index}/{len(schools)}] {school}: running =====", flush=True)
            cmd = [
                sys.executable,
                "scripts/production_school_pipeline.py",
                "--school", school,
                "--input", args.input,
                "--output-root", args.output_root,
                "--departments-root", args.departments_root,
                "--max-pages", str(args.max_pages),
                "--max-depth", str(args.max_depth),
                "--links-per-page", str(args.links_per_page),
                "--profile-links-per-page", str(args.profile_links_per_page),
                "--max-pages-per-host", str(args.max_pages_per_host),
            ]
            if args.allow_external:
                cmd.append("--allow-external")
            if args.enable_ai:
                cmd.append("--enable-ai")
            if args.force:
                cmd.append("--force")
            try:
                proc = subprocess.run(cmd, text=True, capture_output=True, timeout=args.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                status = "timeout"
                message = f"超过单校超时限制 {args.timeout_seconds}s: {exc}"
                print(message, flush=True)
                proc = None
            if proc is not None:
                print((proc.stdout or "")[-3000:], flush=True)
            if proc is not None and proc.returncode != 0:
                status = "failed"
                message = (proc.stderr or proc.stdout or "")[-1000:].replace("\n", " ")
                print(message, flush=True)

        dept_dir = departments_root / school
        rows.append({
            "school": school,
            "status": status,
            "seconds": round(time.time() - start, 1),
            "programs": count_rows(output_dir / "unified_programs.csv"),
            "teachers": count_rows(output_dir / "teachers.csv"),
            "linked_teachers": count_rows(output_dir / "unified_teachers.csv"),
            "candidate_pages": count_rows(output_dir / "candidate_pages.csv"),
            "issues": count_rows(output_dir / "extraction_issues.csv"),
            "department_files": len([p for p in dept_dir.glob("*.md") if p.name != "README.md"]) if dept_dir.exists() else 0,
            "message": message[:300],
        })
        with summary_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    print(f"完成：summary={summary_path}")


if __name__ == "__main__":
    main()
