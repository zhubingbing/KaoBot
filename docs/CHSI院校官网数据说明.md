# CHSI 院校官网数据说明

本文档说明 `grad_school_scraper/chsi_seeds_national.csv` 中院校官网相关字段的口径、覆盖情况和维护脚本。

## 数据范围

- 数据文件：`grad_school_scraper/chsi_seeds_national.csv`
- 记录数：939 条
- 基础来源：中国研究生招生信息网院校库页面
- 官网补充来源：学校或招生单位官方主站，以及主站内可访问的本科教育、专业设置、师资队伍等页面

## 字段口径

### `official_site_url`

学校或招生单位的官方主站首页。

该字段不是研究生院官网、研招办官网、招生网、本科招生网或招聘站。

示例：

- `https://www.pku.edu.cn`
- `https://www.ruc.edu.cn`
- `https://www.tsinghua.edu.cn`

### `external_site_candidates`

用于辅助核验的学校官方主域候选链接。

当前处理会尽量归一到学校官方主域，避免保留具体招生、研究生或公告页面。

### `undergraduate_programs_url`

学校官网主域下的本科专业、本科教育、专业设置、教育教学或人才培养相关页面。

允许的典型页面包括：

- 本科专业
- 专业设置
- 本科教育
- 本科生教育
- 本科教学
- 教务处或本科生院下的专业页面

### `faculty_team_url`

学校官网主域下的师资队伍、教师队伍、师资力量、人才队伍或教师主页平台页面。

不应使用招聘页面、招考页面、新闻详情页或招生页面。

## 当前覆盖情况

截至最近一次整理：

| 字段 | 已补充数量 | 总记录数 |
| --- | ---: | ---: |
| `official_site_url` | 739 | 939 |
| `external_site_candidates` | 739 | 939 |
| `undergraduate_programs_url` | 485 | 939 |
| `faculty_team_url` | 345 | 939 |

剩余空值通常属于以下情况：

- 研招网页面和已知学校主站中没有稳定、明确的对应链接
- 科研院所、军校或非普通本科高校缺少标准本科专业页面
- 学校官网导航结构特殊，无法通过当前规则可靠识别
- 页面需要动态渲染、跳转或访问受限

为空比误填更好。不要用研究生招生页、本科招生页、新闻页或搜索结果硬填。

## 排除规则

以下链接不得写入 `official_site_url`：

- 研究生院、研究生招生、研招办页面
- 本科招生网、本科招生办公室页面
- 招聘、人才招聘、招考页面
- 新闻详情页、公告详情页
- 微信、微博、百度、第三方平台页面
- 邮箱、电话、地图链接

脚本会过滤常见域名或路径特征，例如：

- `yjs`, `yjsc`, `yjsy`, `yjsxy`
- `graduate`, `graduateschool`
- `yz`, `yzb`, `yanzhao`
- `zs`, `zsxx`, `bkzs`, `zsb`
- `admission`, `admissions`
- `job`, `jobs`, `rczp`, `zhaopin`

注意：`zyjs` 在很多学校官网中表示“专业介绍”，不能简单按“研究生”误判。

## 维护脚本

### 更新学校主官网

脚本：

```bash
python3 grad_school_scraper/update_chsi_official_sites.py grad_school_scraper/chsi_seeds_national.csv
```

用途：

- 从研招网页面、院校简介、院系设置等页面中抽取学校官方主站
- 将 `official_site_url` 归一为学校或招生单位主站首页
- 过滤研究生院、招生网、招聘站和第三方链接

### 补充本科专业和师资页面

脚本：

```bash
python3 grad_school_scraper/enrich_official_school_pages.py grad_school_scraper/chsi_seeds_national.csv
```

用途：

- 基于 `official_site_url` 访问学校主站
- 在同一学校官方主域内寻找本科专业/本科教育页面
- 在同一学校官方主域内寻找师资队伍/教师队伍页面
- 新增或更新 `undergraduate_programs_url`、`faculty_team_url`

## 校验建议

提交前建议运行以下检查：

```bash
python3 - <<'PY'
import csv, re
p = 'grad_school_scraper/chsi_seeds_national.csv'
with open(p, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
print('rows', len(rows))
for fld in ['official_site_url', 'undergraduate_programs_url', 'faculty_team_url']:
    print(fld, sum(bool(r.get(fld)) for r in rows))
bad = []
pat = re.compile(
    r'(graduate|graduateschool|admission|admissions|yanzhao|yzb|'
    r'zsxx|yjszs|bkzs|zsb|lqcx|/job|zhaopin|rczp|@)',
    re.I,
)
for r in rows:
    for fld in ['official_site_url', 'undergraduate_programs_url', 'faculty_team_url']:
        url = r.get(fld) or ''
        if pat.search(url):
            bad.append((r['school_name'], fld, url))
print('bad count', len(bad))
for item in bad[:20]:
    print(*item)
PY
```

`bad count` 应为 0。若出现命中，需要人工判断并清理。

## 提交注意事项

- 每次修改前先执行 `git pull --rebase origin main`
- 只提交 CSV、脚本和必要文档
- 不提交本地备份文件，例如：
  - `grad_school_scraper/chsi_seeds_national.csv.bak`
  - `grad_school_scraper/chsi_seeds_national.csv.official-pages.bak`
- 不提交临时目录或未确认来源的数据目录
