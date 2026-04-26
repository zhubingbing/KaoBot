#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path

import pandas as pd


def count_csv(path: Path) -> int:
    if not path.exists():
        return 0
    return len(pd.read_csv(path).fillna(""))


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总智能抽取结果质量")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    out = Path(args.output_dir)
    counts = {
        "candidate_pages": count_csv(out / "candidate_pages.csv"),
        "page_classification": count_csv(out / "page_classification.csv"),
        "departments": count_csv(out / "departments.csv"),
        "programs": count_csv(out / "programs.csv"),
        "teachers": count_csv(out / "teachers.csv"),
        "program_teacher_links": count_csv(out / "program_teacher_links.csv"),
        "unified_teachers": count_csv(out / "unified_teachers.csv"),
        "issues": count_csv(out / "extraction_issues.csv"),
    }
    teachers = pd.read_csv(out / "teachers.csv").fillna("") if (out / "teachers.csv").exists() else pd.DataFrame()
    high_conf_teachers = int((teachers.get("confidence", pd.Series(dtype=float)).astype(float) >= 0.75).sum()) if not teachers.empty else 0
    lines = ["# 智能抽取质量报告", ""]
    for key, value in counts.items():
        lines.append(f"- {key}: {value}")
    lines.append(f"- high_confidence_teachers: {high_conf_teachers}")
    if not teachers.empty and "department" in teachers:
        lines.append("")
        lines.append("## 教师按院系统计")
        for dept, n in teachers.groupby("department").size().sort_values(ascending=False).head(30).items():
            lines.append(f"- {dept or '未识别院系'}: {n}")
    (out / "quality_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out / "quality_report.md")


if __name__ == "__main__":
    main()
