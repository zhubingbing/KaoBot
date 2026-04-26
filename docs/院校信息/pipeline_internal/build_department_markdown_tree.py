#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
from pathlib import Path

import pandas as pd


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_filename(name: str) -> str:
    name = clean(name) or "未识别院系"
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", "", name)
    return name[:120] + ".md"




def host_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", clean(url).lower())
    return m.group(1).removeprefix("www.") if m else ""




def valid_output_department(name: str) -> bool:
    name = clean(name)
    if not name or len(name) > 36:
        return False
    if any(token.lower() in name.lower() for token in [".pdf", ".doc", ".xls", "http", "实施细则", "招生简章", "拟录取", "复试", "名单"]):
        return False
    if re.search(r"20\d{2}", name):
        return False
    return bool(re.search(r"(学院|学系|研究院|研究所|中心|学部|系|校区|体育部)$", name))

def valid_department_label(name: str) -> bool:
    name = clean(name)
    if not name or len(name) > 36:
        return False
    if name in {"电话", "：", "基本情况", "队伍概况", "优秀人才", "产业导师", "硕士生导师", "博士生导师"}:
        return False
    if any(token in name for token in ["。", "，", "；", "、", "电话", "邮箱", "主要从事", "毕业于", "获得者", "研究方向", "聚焦于"]):
        return False
    return bool(re.search(r"(学院|学系|研究院|研究所|中心|学部|系)$", name))

def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path).fillna("")


def valid_teacher_name(name: str) -> bool:
    name = clean(name)
    if not re.fullmatch(r"[\u4e00-\u9fa5·]{2,6}", name):
        return False
    return name not in {"基本情况", "队伍概况", "优秀人才", "产业导师", "硕士生导师", "博士生导师", "软件工程系"}


def canonical_department(department: str, known_departments: set[str]) -> str:
    department = clean(department)
    if not department:
        return ""
    department = re.sub(r"（[^）]{1,30}）", "", department)
    department = department.replace("北京工业大学", "").replace("北京交通大学", "").replace("北京大学", "").replace("清华大学", "").replace("中国人民大学", "")
    department = clean(department)
    if department in known_departments:
        return department
    for known in sorted(known_departments, key=len, reverse=True):
        if known and (known in department or department in known):
            return known
    return department if valid_department_label(department) else ""


def teacher_pool(output_dir: Path) -> pd.DataFrame:
    teachers = read_csv(output_dir / "teachers.csv")
    if not teachers.empty:
        rows = []
        for _, row in teachers.iterrows():
            teacher_name = clean(row.get("teacher_name"))
            if not valid_teacher_name(teacher_name):
                continue
            department = clean(row.get("department")) or clean(row.get("teacher_unit"))
            rows.append({
                "department": department,
                "teacher_name": teacher_name,
                "teacher_title": clean(row.get("title")),
                "teacher_research_fields": clean(row.get("research_fields")),
                "teacher_unit": clean(row.get("teacher_unit")),
                "teacher_profile_url": clean(row.get("teacher_profile_url")),
                "source_url": clean(row.get("source_url")),
                "source_type": "院系教师页",
            })
        return pd.DataFrame(rows).drop_duplicates()

    unified = read_csv(output_dir / "unified_teachers.csv")
    if unified.empty:
        return pd.DataFrame()
    rows = []
    for _, row in unified.iterrows():
        rows.append({
            "department": clean(row.get("department")),
            "teacher_name": clean(row.get("teacher_name")),
            "teacher_title": clean(row.get("teacher_title")),
            "teacher_research_fields": clean(row.get("teacher_research_fields")) or clean(row.get("research_direction")),
            "teacher_unit": clean(row.get("teacher_unit")),
            "teacher_profile_url": clean(row.get("teacher_profile_url")),
            "source_url": clean(row.get("source_url")),
            "source_type": "目录导师/弱关联教师",
        })
    return pd.DataFrame(rows).drop_duplicates(subset=["department", "teacher_name", "teacher_profile_url"])


def build_department_file(
    school_name: str,
    department: str,
    programs: pd.DataFrame,
    teachers: pd.DataFrame,
    linked: pd.DataFrame,
    max_programs: int,
    max_teachers: int,
    max_linked: int,
) -> str:
    dept_programs = programs[programs["department"].astype(str).eq(department)].copy() if not programs.empty else pd.DataFrame()
    dept_teachers = teachers[teachers["department"].astype(str).eq(department)].copy() if not teachers.empty else pd.DataFrame()
    dept_linked = linked[linked["department"].astype(str).eq(department)].copy() if not linked.empty else pd.DataFrame()

    lines = [f"# {school_name} - {department}", ""]
    lines.append("## 概览")
    lines.append(f"- 专业/方向记录：{len(dept_programs)}")
    if not dept_programs.empty and "major_name" in dept_programs:
        lines.append(f"- 专业数量：{dept_programs['major_name'].nunique()}")
    lines.append(f"- 教师记录：{len(dept_teachers)}")
    lines.append(f"- 专业-教师弱关联：{len(dept_linked)}")
    lines.append("")

    lines.append("## 专业/方向")
    if dept_programs.empty:
        lines.append("暂无专业/方向数据。")
    else:
        sort_cols = [c for c in ["level", "major_code", "major_name", "direction_code", "research_direction"] if c in dept_programs.columns]
        if sort_cols:
            dept_programs = dept_programs.sort_values(sort_cols)
        for idx, (_, row) in enumerate(dept_programs.iterrows(), start=1):
            if idx > max_programs:
                lines.append(f"- 其余 {len(dept_programs) - max_programs} 条专业/方向略。")
                break
            level = clean(row.get("level"))
            code = clean(row.get("major_code"))
            major = clean(row.get("major_name"))
            direction = clean(row.get("research_direction"))
            study_mode = clean(row.get("study_mode"))
            title = " ".join(x for x in [level, code, major] if x)
            if direction:
                title += f" / {direction}"
            if study_mode:
                title += f"（{study_mode}）"
            lines.append(f"- {title}")
    lines.append("")

    lines.append("## 院系教师池")
    lines.append("说明：这里是该院系抓到的教师，不等同于每个专业的官方导师名单。")
    if dept_teachers.empty:
        lines.append("暂无教师数据。")
    else:
        dept_teachers = dept_teachers.sort_values(["teacher_name", "teacher_title"])
        for idx, (_, row) in enumerate(dept_teachers.iterrows(), start=1):
            if idx > max_teachers:
                lines.append(f"- 其余 {len(dept_teachers) - max_teachers} 位教师略。")
                break
            name = clean(row.get("teacher_name"))
            title = clean(row.get("teacher_title"))
            fields = clean(row.get("teacher_research_fields"))
            unit = clean(row.get("teacher_unit"))
            profile = clean(row.get("teacher_profile_url"))
            bit = name
            extras = []
            if title:
                extras.append(title)
            if unit:
                extras.append(unit)
            if fields:
                extras.append(fields)
            if extras:
                bit += "（" + "；".join(extras) + "）"
            if profile:
                bit += f" [主页]({profile})"
            lines.append(f"- {bit}")
    lines.append("")

    lines.append("## 专业-教师弱关联")
    lines.append("说明：按教师研究方向与专业方向关键词匹配，仅供筛选和复核。")
    if dept_linked.empty:
        lines.append("暂无弱关联数据。")
    else:
        sort_cols = [c for c in ["major_name", "research_direction", "teacher_name"] if c in dept_linked.columns]
        if sort_cols:
            dept_linked = dept_linked.sort_values(sort_cols)
        for idx, (_, row) in enumerate(dept_linked.iterrows(), start=1):
            if idx > max_linked:
                lines.append(f"- 其余 {len(dept_linked) - max_linked} 条弱关联略。")
                break
            level = clean(row.get("level"))
            major = clean(row.get("major_name"))
            direction = clean(row.get("research_direction"))
            teacher = clean(row.get("teacher_name"))
            title = clean(row.get("teacher_title"))
            match = clean(row.get("match_type"))
            program = " ".join(x for x in [level, major] if x)
            if direction:
                program += f" / {direction}"
            teacher_text = teacher + (f"（{title}）" if title else "")
            lines.append(f"- {program}：{teacher_text}；{match}")
    lines.append("")

    lines.append("## 数据来源")
    source_urls = []
    for df in [dept_programs, dept_teachers, dept_linked]:
        if not df.empty and "source_url" in df.columns:
            source_urls.extend(clean(x) for x in df["source_url"].tolist() if clean(x))
    for url in list(dict.fromkeys(source_urls))[:30]:
        lines.append(f"- {url}")
    if len(set(source_urls)) > 30:
        lines.append(f"- 其余 {len(set(source_urls)) - 30} 个来源略。")
    return "\n".join(lines) + "\n"


def build_tree(school_name: str, output_dir: Path, root: Path, max_programs: int, max_teachers: int, max_linked: int) -> None:
    programs = read_csv(output_dir / "unified_programs.csv")
    teachers = teacher_pool(output_dir)
    linked = read_csv(output_dir / "unified_teachers.csv")
    known_program_departments = set()
    if not programs.empty and "department" in programs:
        programs["department"] = programs["department"].apply(clean)
        programs = programs[programs["department"].apply(valid_output_department)].copy()
        known_program_departments = {clean(x) for x in programs["department"].tolist() if valid_output_department(x)}
    if not teachers.empty and "department" in teachers:
        if known_program_departments:
            host_department = {}
            for _, row in teachers.iterrows():
                raw_dept = clean(row.get("department"))
                canonical = canonical_department(raw_dept, known_program_departments)
                host = host_of(row.get("source_url")) or host_of(row.get("teacher_profile_url"))
                if host and canonical in known_program_departments:
                    host_department[host] = canonical
            normalized = []
            for _, row in teachers.iterrows():
                host = host_of(row.get("source_url")) or host_of(row.get("teacher_profile_url"))
                dept = host_department.get(host) or canonical_department(row.get("department"), known_program_departments)
                normalized.append(dept if dept in known_program_departments else "")
            teachers["department"] = normalized
            teachers = teachers[teachers["department"].astype(str).str.len() > 0].copy()
        else:
            teachers["department"] = teachers["department"].apply(lambda x: canonical_department(x, set()))
            teachers = teachers[teachers["department"].astype(str).str.len() > 0].copy()
    if not linked.empty and "department" in linked:
        if known_program_departments:
            linked["department"] = linked["department"].apply(lambda x: canonical_department(x, known_program_departments))
            linked = linked[linked["department"].isin(known_program_departments)].copy()
        else:
            linked["department"] = linked["department"].apply(lambda x: canonical_department(x, set()))
            linked = linked[linked["department"].astype(str).str.len() > 0].copy()

    school_dir = root / school_name
    school_dir.mkdir(parents=True, exist_ok=True)
    for old_md in school_dir.glob("*.md"):
        old_md.unlink()

    departments = set()
    if not programs.empty and "department" in programs:
        departments.update(clean(x) for x in programs["department"].tolist() if valid_output_department(x))
    if not teachers.empty and "department" in teachers:
        departments.update(clean(x) for x in teachers["department"].tolist() if valid_output_department(x))
    if not linked.empty and "department" in linked:
        departments.update(clean(x) for x in linked["department"].tolist() if valid_output_department(x))

    index_lines = [f"# {school_name} 院系目录", ""]
    index_lines.append(f"- 院系文件数：{len(departments)}")
    index_lines.append(f"- 专业/方向记录：{len(programs)}")
    index_lines.append(f"- 教师记录：{len(teachers)}")
    index_lines.append(f"- 专业-教师弱关联：{len(linked)}")
    index_lines.append("")

    for dept in sorted(departments):
        filename = safe_filename(dept)
        path = school_dir / filename
        content = build_department_file(school_name, dept, programs, teachers, linked, max_programs, max_teachers, max_linked)
        path.write_text(content, encoding="utf-8")
        index_lines.append(f"- [{dept}](./{filename})")

    (school_dir / "README.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="按 departments/学校/院系.md 生成中文院系专业教师目录")
    parser.add_argument("--school", action="append", nargs=2, metavar=("学校名", "输出目录"), required=True)
    parser.add_argument("--root", default="departments")
    parser.add_argument("--max-programs", type=int, default=200)
    parser.add_argument("--max-teachers", type=int, default=200)
    parser.add_argument("--max-linked", type=int, default=200)
    args = parser.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    for school_name, output_dir in args.school:
        build_tree(school_name, Path(output_dir), root, args.max_programs, args.max_teachers, args.max_linked)
        print(root / school_name)


if __name__ == "__main__":
    main()
