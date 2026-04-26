# 高校院系专业采集 Pipeline

生产入口只有一个：

```bash
python scripts/production_school_pipeline.py --school 学校名
```

目标输出：

```text
departments/学校/README.md
departments/学校/院系.md
```

当前策略：

- 优先使用 Crawl4AI 抓官网。
- 先做学校、院系、专业目录。
- 院系教师池属于主流程：官网能发现师资页就自动采集；不依赖 Tavily。
- 专业-教师强绑定不做，弱关联后置。
- Tavily 默认禁用，只作为以后补缺入口。
- 用户不需要手动运行内部 adapter。


## 架构分工

```text
Crawl4AI 负责“看网页”。
LLM 负责“理解不规则内容”。
规则 adapter 负责“稳定结构化”。
Tavily 负责“找不到时补入口”，默认禁用。
Markdown 生成器负责“最终可读结果”。
```

详细设计见：

```text
docs/CRAWL4AI_PRODUCTION_PIPELINE.md
```

## 环境

```bash
source .venv/bin/activate
pip install -U crawl4ai
crawl4ai-setup
crawl4ai-doctor
python -m playwright install chromium
```

## 单校运行

```bash
source .venv/bin/activate
CRAWL4_AI_BASE_DIRECTORY="$(pwd)" \
python scripts/production_school_pipeline.py \
  --school 中国人民大学 \
  --max-pages 120 \
  --max-depth 2 \
  --links-per-page 30 \
  --profile-links-per-page 80 \
  --allow-external
```

说明：

- `--school` 从 `院校信息.csv` 读取官网地址。
- `--allow-external` 用于允许官方相关子域，例如研究生院、招生网等。
- 默认不使用 Tavily。
- 默认使用 Crawl4AI。

## 输出

每所学校输出在：

```text
output/school_finals/{school_slug}_final/
```

核心文件：

```text
unified_programs.csv      # 统一专业表
teachers.csv              # 院系教师池，官网发现师资页时自动填充
unified_teachers.csv      # 专业-教师弱关联，可为空，不作为官方导师关系
departments.csv           # 院系表
candidate_pages.csv       # 官网发现的候选页面
page_classification.csv   # 页面分类结果
pipeline_summary.md       # 执行摘要
```

最终 Markdown：

```text
departments/{学校}/README.md
departments/{学校}/{院系}.md
```

## 目录说明

```text
scripts/production_school_pipeline.py  # 唯一用户入口
pipeline_internal/                     # 内部实现，不直接运行
output/school_finals/                  # 干净的最终结构化输出
departments/                           # 面向阅读的中文院系目录
```

## 当前已有结果

```text
departments/北京大学/
departments/清华大学/
departments/中国人民大学/
```

## 生产流程

```text
院校信息.csv
  -> Crawl4AI 官网抓取
  -> 官网链接发现和页面分类
  -> 院系/招生/专业目录入口识别
  -> PDF 或 HTML 专业目录 adapter
  -> unified_programs.csv
  -> departments/学校/院系.md
```
