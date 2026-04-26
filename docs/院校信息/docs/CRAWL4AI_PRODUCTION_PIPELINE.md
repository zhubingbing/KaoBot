# Crawl4AI 优先的高校信息采集生产方案

## 目标

面向几百所高校，建立一条稳定、可复用、少定制的采集链路，最终输出：

```text
学校 -> 院系 -> 本科/硕士/博士专业 -> 教师池（后置增强）
```

当前优先级：

1. 学校官网信息
2. 院系/机构信息
3. 本科、硕士、博士专业目录
4. 院系教师池
5. 专业-教师弱关联

院系教师池属于主流程：官网抓取时如果发现师资队伍/教师列表/教师详情页，就自动采集到 `teachers.csv`。但“专业-教师强绑定”不做，专业-教师弱关联后置。

## 基本原则

### 1. Crawl4AI 优先

生产版主抓取引擎统一使用 Crawl4AI。

Crawl4AI 负责：

- 官网页面抓取
- JS 渲染
- Clean/Fit Markdown 生成
- 链接提取
- 页面正文清洗
- 表格/列表页面转成更适合 LLM 和规则解析的内容
- 后续可接 LLM schema extraction

不再把 `requests` 作为主路径。`requests` 只允许作为极小范围的底层 fallback 或调试工具，不作为生产入口。

### 2. Tavily 默认禁用

Tavily 不是主链路。

Tavily 只作为候补：

```text
官网 Crawl4AI 发现不足 -> 缺口检测 -> 手动或配置开启 Tavily 补入口
```

默认不启用 Tavily，避免过度依赖搜索 API、成本不可控、入口漂移。

### 3. 不按学校定制主流程

不能给每个学校写一个脚本。

允许存在 adapter，但 adapter 必须按“页面/数据源类型”划分，而不是按学校划分：

- HTML 招生专业目录表格 adapter
- PDF 招生专业目录 adapter
- 官网院系列表 adapter
- 教师列表/详情页 adapter

不推荐：

```text
collect_pku_xxx.py
collect_ruc_xxx.py
collect_xxx_school.py
```

这类脚本只能作为临时验证样本，不能进入生产主链路。

## 生产架构

```text
院校信息.csv
  -> school_pipeline 主入口
    -> Crawl4AI 官网深度抓取
      -> Clean/Fit Markdown
      -> 链接抽取
      -> 页面分类
        -> 学校概况页
        -> 院系/机构页
        -> 本科专业页
        -> 研究生招生目录页
        -> 博士招生目录页
        -> 教师/师资页
      -> 官方入口评分
      -> 数据源 adapter
        -> HTML 表格目录解析
        -> PDF 目录解析
        -> Markdown/LLM schema 抽取
      -> 统一 CSV
        -> departments.csv
        -> unified_programs.csv
        -> teachers.csv
        -> unified_teachers.csv
      -> departments/学校/院系.md
```

## Pipeline 阶段设计

### 阶段 1：学校官网抓取

输入：

```text
school_name
official_site_url
```

Crawl4AI 从官网首页开始抓取，限制在官方域名及可信子域。

输出：

```text
pages/*.html
pages_markdown/*.raw.md
pages_markdown/*.fit.md
candidate_pages.csv
page_classification.csv
```

页面分类字段：

```text
department
program
teacher_list
teacher_profile
admission
news
other
```

### 阶段 2：院系发现

优先从官网以下页面发现：

- 院系设置
- 组织机构
- 学部与院系
- 教学机构
- 科研机构
- 学院导航

输出：

```text
departments.csv
```

字段：

```text
school_name
department
division
site_url
source_url
confidence
extract_method
```


### 阶段 2.5：院系官网二阶段扩展

很多学校的主站只给学院独立站入口，真正的专业、师资页面在学院子域。生产流程会自动维护：

```text
院系名 -> 学院官网域名
```

后续在同一学院域名下发现的师资页、教师详情页，会优先归并到该学院，而不是把桥梁工程系、岩土工程系等内设系所作为顶层院系。

示例：

```text
https://www.bjtu.edu.cn/ -> https://civil.bjtu.edu.cn -> 土木建筑工程学院
https://yanzhao.bjut.edu.cn/zsxy1.htm -> https://cs.bjut.edu.cn -> 计算机学院
```

链接调度策略：

- `师资/教师/导师/szdw/szll/jsml/faculty` 链接提权。
- `复试/拟录取/调剂/名单/通知/公告/新闻` 链接降权。
- PDF、Word、Excel 附件不进入主网页发现队列，交给专业目录 adapter。

### 阶段 3：专业目录发现

从官网和研究生院/招生网中发现：

- 本科专业
- 硕士招生专业目录
- 博士招生专业目录
- 招生简章目录
- 专业学位目录

候选入口包括：

```text
专业
专业目录
招生目录
硕士招生
博士招生
研究生招生
本科专业
培养方案
```

### 阶段 4：专业目录解析 adapter

#### HTML 表格 adapter

适用页面：人大 2026 硕士/博士目录这类 HTML 表格。

典型表头：

```text
院系所 / 专业 / 研究方向 / 学习方式 / 考试科目
学院 / 专业 / 方向 / 外国语 / 加试 / 备注
```

输出统一到：

```text
unified_programs.csv
```

#### PDF adapter

适用页面：北大/清华这类 PDF 专业目录。

输出同样统一到：

```text
unified_programs.csv
```

#### Markdown + LLM schema adapter

适合结构不稳定但页面内容清楚的场景。

Crawl4AI 生成 Fit Markdown 后，LLM schema 只做结构化提取，不负责大范围网页发现。

建议 schema：

```json
{
  "programs": [
    {
      "department": "",
      "level": "本科|硕士|博士|专业学位",
      "major_code": "",
      "major_name": "",
      "research_direction": "",
      "study_mode": "",
      "exam_subjects": "",
      "note": "",
      "source_url": ""
    }
  ]
}
```

### 阶段 5：院系教师池

院系教师池是主 pipeline 的官网解析能力，不依赖 Tavily。

只要 Crawl4AI 在官网中发现师资队伍、在职教职工、教师列表、教师详情页，就写入 `teachers.csv`，并在最终 Markdown 中生成“院系教师池”。

教师采集优先级：

1. 院系官网师资队伍页
2. 在职教职工页
3. 教师列表页
4. 教师详情页
5. Tavily 搜索补入口

教师结果只作为“院系教师池”，不强行绑定专业。专业-教师关系只做弱关联，并明确标注为复核线索。

### 阶段 6：Tavily 候补补缺

默认关闭。

触发条件：

- 官网 Crawl4AI 没发现研究生专业目录
- 官网 Crawl4AI 没发现院系列表
- 某院系专业记录明显为 0
- 用户明确要求补某个入口

搜索关键词模板：

```text
{学校名} 院系设置
{学校名} 教学机构
{学校名} 本科专业
{学校名} 研究生 招生专业目录
{学校名} 硕士 专业目录
{学校名} 博士 专业目录
{学校名} {院系名} 师资队伍
```

Tavily 结果必须经过官方域名过滤。

第三方页面只作为发现线索，不入正式数据。

## 当前脚本整理建议

### 生产主入口

保留并收敛：

```text
scripts/production_school_pipeline.py
```

未来所有学校都从这个入口跑。

建议生产命令：

```bash
source .venv/bin/activate
CRAWL4_AI_BASE_DIRECTORY="$(pwd)" \
python scripts/production_school_pipeline.py \
  --school 中国人民大学 \
  --site https://www.ruc.edu.cn/ \
  --output-dir output/school_finals/ruc_final \
  --engine crawl4ai \
  --max-pages 120 \
  --max-depth 2 \
  --links-per-page 30 \
  --allow-external
```

### 专业目录 adapter

保留，但应由 pipeline 自动调用：

```text
pipeline_internal/parse_admission_pdfs.py
pipeline_internal/parse_admission_html_tables.py
```

这两个脚本不应作为用户长期手动入口，而应是 pipeline 内部 adapter。

### Markdown 输出生成

保留：

```text
pipeline_internal/build_department_markdown_tree.py
```

它是最终目录生成器，输入统一 CSV，输出：

```text
departments/学校/院系.md
```

使用：

```bash
python pipeline_internal/build_department_markdown_tree.py \
  --school 北京大学 output/school_finals/pku_intelligence_current \
  --school 清华大学 output/school_finals/tsinghua_final \
  --school 中国人民大学 output/school_finals/ruc_final \
  --root departments \
  --max-programs 200 \
  --max-teachers 200 \
  --max-linked 200
```

### Tavily 相关脚本

保留但默认禁用：

```text
scripts/search_school_web.py
```

只在补缺时使用。

不应该在主流程默认调用。

### 临时/样本脚本

这些不应进入生产主流程：

```text
scripts/collect_pku_ues_teachers.py
scripts/collect_pku_sess_teachers.py
scripts/collect_pku_math_teachers.py
scripts/collect_pku_all_teachers.py
scripts/collect_pku_catalogs.py
scripts/collect_tsinghua_grad_catalogs.py
scripts/curate_tsinghua_summary.py
scripts/build_tsinghua_unified_summary.py
scripts/enrich_school_departments_from_search.py
```

处理建议：

```text
scripts/archive/
```

或保留但标记为：

```text
experimental / legacy / school-specific
```

不要让这些脚本成为生产入口。

## Crawl4AI 使用规范

### 版本

当前本地版本是：

```text
crawl4ai 0.8.6
```

如果要使用你提到的 v0.8.6 能力，需要升级并重新验证：

```bash
source .venv/bin/activate
pip install -U crawl4ai
crawl4ai-setup
crawl4ai-doctor
python -m playwright install chromium
```

### 抓取配置建议

生产默认：

```text
headless=True
cache_mode=ENABLED
markdown=Clean + Fit Markdown
same-domain first
low concurrency
resume enabled
```

页面内容策略：

```text
HTML 用于表格/链接精确解析
Fit Markdown 用于 LLM/schema 抽取
Raw Markdown 用于审计和回放
```

### 不建议

- 不建议默认 Tavily 搜索。
- 不建议默认全站无限深度爬。
- 不建议每所学校写专用脚本。
- 不建议把老师作为专业目录前置依赖；但院系教师池属于主流程自动采集。
- 不建议把第三方搜索结果作为正式来源。

## 统一输出规范

每所学校最终输出目录：

```text
output/school_finals/{school_slug}/
  pipeline_summary.md
  candidate_pages.csv
  page_classification.csv
  departments.csv
  unified_programs.csv
  teachers.csv
  unified_teachers.csv
  extraction_issues.csv
  adapter_results.json
```

最终用户查看目录：

```text
departments/{学校}/README.md
departments/{学校}/{院系}.md
```

院系 Markdown 格式：

```text
# 学校 - 院系

## 概览
- 专业/方向记录
- 专业数量
- 教师记录
- 专业-教师弱关联

## 专业/方向

## 院系教师池

## 专业-教师弱关联

## 数据来源
```

## 质量门槛

专业数据质量：

- 必须有 `department`
- 必须有 `major_name`
- 硕士/博士优先有 `major_code`
- 必须有 `source_url`
- 来源必须是官方域名

院系数据质量：

- 名称不能是导航词
- 不能是新闻标题
- 不能是“更多/首页/招生/招聘”等菜单词
- 优先保留官网组织机构、院系设置、招生目录中的院系名

教师数据质量：

- 教师后置
- 先作为院系教师池
- 不强行做专业绑定
- 没有详情页或研究方向时也可以保留，但置信度降低

## 当前状态

已经有：

```text
departments/北京大学/
departments/清华大学/
departments/中国人民大学/
```

人大当前：

```text
专业/方向记录：608
院系文件数：47
教师记录：0
```

人大结果目前专业目录已经能生成，但还需要把 HTML adapter 正式接入主 pipeline，而不是手动单独跑。

## 下一步整理任务

1. 升级 Crawl4AI 到目标版本并跑 doctor。
2. 禁用 Tavily 默认入口。
3. 把 HTML/PDF adapter 接入 `school_intelligence_pipeline.py`。
4. 把生产主入口固定为 `school_intelligence_pipeline.py`。
5. 把学校专用脚本移动到 `scripts/archive/` 或标记 legacy。
6. 先批量跑“学校-院系-专业-院系教师池”，专业目录是主验收项，教师池随官网发现自动补。
7. 对缺口学校再启用 Tavily 补入口。

## 组件职责分工

### 总体链路

```text
院校信息.csv
  -> production_school_pipeline.py
    -> Crawl4AI 官网抓取
    -> HTML / Markdown / Links 标准化
    -> 规则 + LLM 页面理解
    -> PDF / HTML 专业目录 adapter
    -> unified_programs.csv
    -> departments/学校/院系.md
```

### Crawl4AI 负责什么

Crawl4AI 是网页采集和内容清洗层。

负责：

- 打开学校官网、研究生院、招生网、院系官网。
- 渲染 JavaScript 页面。
- 抽取页面 HTML。
- 生成 Clean Markdown / Fit Markdown。
- 提取页面内部链接。
- 保留 `source_url`。
- 处理动态页面、iframe、懒加载、滚动页面。
- 为规则解析和 LLM schema extraction 提供统一输入。

不负责：

- 判断最终字段含义。
- 生成统一 CSV。
- 生成 `departments/学校/院系.md`。
- 直接判断专业和老师的官方关系。

### LLM 负责什么

LLM 是语义理解和结构化辅助层。

负责：

- 判断页面类型：院系页、招生目录页、本科专业页、硕士专业页、博士专业页、教师页。
- 从非标准 Markdown 中提取结构化字段。
- 对复杂页面做 schema extraction。
- 在规则解析失败时辅助提取：院系名称、专业名称、培养层次、研究方向、学习方式、考试科目。
- 给候选结果提供置信度和复核理由。
- 判断页面是否值得继续深挖。

不负责：

- 大规模网页抓取。
- 搜索全网。
- 直接写最终文件。
- 替代稳定表格解析。
- 硬绑定“专业-老师”官方关系。

### 规则和 Adapter 负责什么

规则和 adapter 是稳定结构解析层。

负责：

- PDF 专业目录解析。
- HTML 表格专业目录解析。
- 固定表头映射，例如：`院系所`、`专业`、`研究方向`、`学习方式`、`考试科目`。
- 输出统一字段：`department`、`major_code`、`major_name`、`level`、`research_direction`、`study_mode`、`source_url`。

原则：

```text
规则能稳定解析的，不交给 LLM。
规则解析不了但内容清楚的，再交给 LLM。
```

### Tavily 负责什么

Tavily 默认禁用，只是缺口补入口工具。

只在这些情况使用：

- 官网 Crawl4AI 没找到院系页。
- 官网 Crawl4AI 没找到招生专业目录。
- 某个院系专业为空。
- 用户明确要求补某个院系。

Tavily 只负责发现入口，不负责入库。

Tavily 结果必须经过：

```text
官方域名过滤 -> Crawl4AI 抓取 -> 规则/LLM 解析 -> source_url 留痕
```

### Markdown 生成器负责什么

`build_department_markdown_tree` 是最终可读结果生成层。

输入：

```text
unified_programs.csv
teachers.csv
unified_teachers.csv
```

输出：

```text
departments/{学校}/README.md
departments/{学校}/{院系}.md
```

它不抓网页，也不解析网页，只把统一 CSV 转成人可读目录。

### 一句话分工

```text
Crawl4AI 负责“看网页”。
LLM 负责“理解不规则内容”。
规则 adapter 负责“稳定结构化”。
Tavily 负责“找不到时补入口”。
Markdown 生成器负责“最终可读结果”。
```

## 用户使用体验

用户只运行一个脚本：

```bash
python scripts/production_school_pipeline.py --school 中国人民大学
```

内部流程自动完成：

```text
官网抓取 -> 院系/专业入口发现 -> 专业目录解析 -> CSV 统一 -> Markdown 生成
```

用户不需要手动运行内部 adapter。

