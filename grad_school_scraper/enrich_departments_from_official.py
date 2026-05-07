#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import datetime as dt
import html
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

DEFAULT_TARGET_NAMES = [
    "西安工业大学", "西安建筑科技大学", "西安科技大学", "西安石油大学", "陕西科技大学", "西安工程大学",
    "长安大学", "西北农林科技大学", "陕西中医药大学", "陕西师范大学", "延安大学", "陕西理工大学",
    "宝鸡文理学院", "咸阳师范学院", "西安外国语大学", "西北政法大学", "西安体育学院", "西安音乐学院",
    "西安美术学院", "西安文理学院", "榆林学院", "商洛学院", "安康学院", "西安财经大学", "西安邮电大学",
    "西安医学院", "西京学院", "西安热工研究院有限公司", "中国航空研究院(603所)", "中国航空研究院(623所)",
    "中国航空研究院(630所)", "中国航空研究院(631所)", "中国航空研究院(618所)",
    "中国兵器科学研究院(西安近代化学研究所)", "中国兵器科学研究院(西安应用光学研究所)",
    "中国兵器科学研究院(西安机电信息技术研究所)", "中国兵器科学研究院(陕西应用物理化学研究所)",
    "中国兵器科学研究院(西北机电工程研究所)", "中国兵器科学研究院(西安现代控制技术研究所)",
    "中国兵器科学研究院(西安电子工程研究所)",
]

CSV_PATH = Path("grad_school_scraper/chsi_seeds_national.csv")
BASE = Path("docs/院校信息/departments")

FALLBACK_SITE = {
    "西安热工研究院有限公司": "https://www.tpri.com.cn",
    "中国航空研究院(603所)": "https://www.avic.com",
    "中国航空研究院(623所)": "https://www.avic.com",
    "中国航空研究院(630所)": "https://www.avic.com",
    "中国航空研究院(631所)": "https://www.avic.com",
    "中国航空研究院(618所)": "https://www.avic.com",
    "中国兵器科学研究院(西安近代化学研究所)": "https://www.norincogroup.com.cn",
    "中国兵器科学研究院(西安应用光学研究所)": "https://www.norincogroup.com.cn",
    "中国兵器科学研究院(西安机电信息技术研究所)": "https://www.norincogroup.com.cn",
    "中国兵器科学研究院(陕西应用物理化学研究所)": "https://www.norincogroup.com.cn",
    "中国兵器科学研究院(西北机电工程研究所)": "https://www.norincogroup.com.cn",
    "中国兵器科学研究院(西安现代控制技术研究所)": "https://www.norincogroup.com.cn",
    "中国兵器科学研究院(西安电子工程研究所)": "https://www.norincogroup.com.cn",
}

KEYWORDS = ("学院", "学部", "院系", "系", "研究院", "中心")
BAD_TEXT = ("新闻", "通知", "招生", "就业", "图书馆", "校友", "附属")


def norm(s: str) -> str:
    return s.replace("（", "(").replace("）", ")").replace(" ", "").strip()


def load_rows():
    with CSV_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_name = {r["school_name"].strip(): r for r in rows}
    by_norm = {norm(k): v for k, v in by_name.items()}
    return by_name, by_norm


def fetch(url: str, timeout=20) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030", "gbk", "big5"):
        try:
            return raw.decode(enc, errors="ignore")
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def extract_links(page_url: str, h: str):
    out = []
    parsed = urlparse(page_url)
    host = parsed.netloc
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', h, re.I | re.S):
        href, text = m.group(1).strip(), m.group(2)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(re.sub(r"\s+", "", text))
        if not text:
            continue
        if not any(k in text for k in KEYWORDS):
            continue
        if any(b in text for b in BAD_TEXT):
            continue
        full = urljoin(page_url, href)
        p = urlparse(full)
        if p.scheme not in ("http", "https"):
            continue
        if p.netloc and host and host not in p.netloc:
            continue
        out.append((text[:40], full))
    dedup = []
    seen = set()
    for t, u in out:
        k = (t, u)
        if k in seen:
            continue
        seen.add(k)
        dedup.append((t, u))
    return dedup[:80]


def extract_nav_targets(page_url: str, h: str):
    targets = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', h, re.I | re.S):
        href, text = m.group(1).strip(), m.group(2)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(re.sub(r"\s+", "", text))
        if not text:
            continue
        if not any(k in text for k in ("院系", "学院", "学部", "机构设置", "教学单位")):
            continue
        full = urljoin(page_url, href)
        if full.startswith("http"):
            targets.append(full)
    out = []
    seen = set()
    for u in targets:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:12]


def render(name, official, source, links, note):
    today = dt.date.today().isoformat()
    lines = [
        f"# {name}院系官网链接文档",
        "",
        "## 文档信息",
        "",
        f"- 学校：{name}",
        f"- 学校官网：{official}",
        f"- 来源页面：{source}",
        f"- 文档生成日期：{today}",
        f"- 院系/相关单位链接数：{len(links)}",
        "",
        "## 院系与相关单位官方链接",
        "",
        "| 序号 | 单位名称 | 官方链接 |",
        "|---|---|---|",
    ]
    for i, (t, u) in enumerate(links, 1):
        lines.append(f"| {i} | {t} | {u} |")
    lines += ["", "## 抓取备注", "", f"- {note}", ""]
    return "\n".join(lines)


def candidate_urls(official: str, row: dict):
    urls = [official]
    for k in ("undergraduate_programs_url", "faculty_team_url"):
        v = (row.get(k, "") if row else "").strip()
        if v:
            urls.append(v)
    common_paths = [
        "/jgsz.htm", "/jgsz.htm", "/xysz.htm", "/xy/index.htm", "/xy/",
        "/yxsz.htm", "/yyjg.htm", "/xueyuan/", "/xygk/jgsz.htm",
    ]
    for p in common_paths:
        urls.append(urljoin(official, p))
    dedup = []
    seen = set()
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def parse_target_names():
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        dirs = []
        for p in sorted(BASE.iterdir()):
            if p.is_dir() and (p / "README.md").exists():
                dirs.append(p.name)
        limit = None
        offset = 0
        if "--limit" in sys.argv:
            i = sys.argv.index("--limit")
            if i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        if "--offset" in sys.argv:
            i = sys.argv.index("--offset")
            if i + 1 < len(sys.argv):
                offset = int(sys.argv[i + 1])
        if offset:
            dirs = dirs[offset:]
        if limit is not None:
            dirs = dirs[:limit]
        return dirs
    return DEFAULT_TARGET_NAMES


def current_link_count(readme: Path) -> int:
    try:
        t = readme.read_text(encoding="utf-8")
    except Exception:
        return 0
    c = 0
    for line in t.splitlines():
        if line.startswith("| ") and "---" not in line and "序号" not in line:
            c += 1
    return c


def main():
    by_name, by_norm = load_rows()
    target_names = parse_target_names()
    changed = 0
    skipped = 0
    for name in target_names:
        readme = BASE / name / "README.md"
        # Skip already enriched files by default in all-mode.
        if len(sys.argv) > 1 and sys.argv[1] == "--all" and current_link_count(readme) > 1:
            skipped += 1
            continue
        row = by_name.get(name) or by_norm.get(norm(name))
        source = row.get("yz_detail_url", "").strip() if row else "https://yz.chsi.com.cn"
        official = row.get("official_site_url", "").strip() if row else ""
        if (not official) or ("***" in official) or ("cdgdc.edu.cn" in official):
            official = FALLBACK_SITE.get(name, official if official else "https://yz.chsi.com.cn")
        links = []
        note = "本次先建立学校级入口，后续继续补抓“院系设置/学院列表”页面。"
        try:
            merged = []
            seen = set()
            nav_targets = []
            for u in candidate_urls(official, row or {}):
                try:
                    page = fetch(u)
                except Exception:
                    continue
                nav_targets.extend(extract_nav_targets(u, page))
                for t, link in extract_links(u, page):
                    if link in seen:
                        continue
                    seen.add(link)
                    merged.append((t, link))
            for u in nav_targets[:10]:
                try:
                    page = fetch(u)
                except Exception:
                    continue
                for t, link in extract_links(u, page):
                    if link in seen:
                        continue
                    seen.add(link)
                    merged.append((t, link))
            links = merged[:80]
            if links:
                note = "已从学校官网多入口页面自动抽取院系相关链接，建议后续人工复核与补全。"
            else:
                links = [(f"{name}官网入口", official)]
        except Exception:
            links = [(f"{name}官网入口", official)]
        if name in FALLBACK_SITE:
            note = "当前使用上级单位官网或院所官网入口，后续补充该单位独立院系页（待复核）。"
        text = render(name, official, source, links, note)
        p = readme
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        changed += 1
    print(f"updated {changed} files, skipped {skipped} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
