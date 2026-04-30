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
docs/UNIVERSITY_CRAWLER_ARCHITECTURE.md
```

## 环境

默认使用 Docker 部署的 Crawl4AI 服务。

### 默认推荐：Docker 模式

```bash
bash ../scripts/deploy_crawl4ai.sh up
bash ../scripts/deploy_crawl4ai.sh test
```

说明：

- 默认推荐 `crawl4ai_docker`
- 不要求本机手工安装 Playwright Chromium
- Docker 服务正常后，再运行学校主流程

### 备用方案：本地浏览器模式

只有在 Docker 不可用时，才考虑本地模式：

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
  --crawler-engine crawl4ai_docker \
  --max-pages 120 \
  --max-depth 2 \
  --links-per-page 30 \
  --profile-links-per-page 80 \
  --allow-external
```

说明：

- `--school` 从 `院校信息.csv` 读取官网地址。
- `--crawler-engine crawl4ai_docker` 为默认推荐模式。
- `--allow-external` 用于允许官方相关子域，例如研究生院、招生网等。
- 默认不使用 Tavily。
- 默认推荐 Docker 方式运行 Crawl4AI。

## 配置文件

正常生产只需要优先维护两个配置：

```text
configs/school_pipeline_sources.csv
configs/department_overrides.csv
```

### 1. 学校入口配置

文件：

```text
configs/school_pipeline_sources.csv
```

清华大学示例：

```csv
school_name,official_site_url,entry_url,entry_type,crawl_mode,notes
清华大学,https://www.tsinghua.edu.cn/,https://www.tsinghua.edu.cn/yxsz.htm,department_index,config_only,清华院系设置页；先从院系入口开始，不做主站盲爬
```

北京工业大学示例：

```csv
school_name,official_site_url,entry_url,entry_type,crawl_mode,notes
北京工业大学,https://www.bjut.edu.cn/,https://www.bjut.edu.cn/,seed_url,append,从学校官网首页开始；先让主流程自动发现院系入口、专业入口和教师入口
```

建议人工优先维护：

- 学校主站 URL
- 院系入口 URL

如果你暂时没有现成的院系列表页，就先这样配置：

- `entry_url=学校官网首页`
- `entry_type=seed_url`

含义：

- 从学校官网首页开始抓
- 让主流程自己发现院系、专业、教师相关入口
- 这是“从学校开始入手”的标准模式

### 2. 单院系补丁配置

文件：

```text
configs/department_overrides.csv
```

示例：

```csv
school_name,department,url,url_type,mode,notes
清华大学,电子工程系,https://www.ee.tsinghua.edu.cn/ryqk/teacher/xxgdzyjs/js2.htm,teacher_group,append,人工确认在职教师页
清华大学,车辆与运载学院,https://www.svm.tsinghua.edu.cn/column/26_1.html,teacher_hub,append,人工补充教师入口
清华大学,科学史系,https://www.dhs.tsinghua.edu.cn/?page_id=2087,teacher_group,append,人工确认教师页
清华大学,安全科学学院,https://www.ssafs.tsinghua.edu.cn/szdw/zgj.htm,teacher_group,append,人工确认专职教师页
```

字段说明：

- `school_name`：学校名
- `department`：院系名
- `url`：人工补充的高价值入口
- `url_type`：
  - `department_site`
  - `teacher_hub`
  - `teacher_group`
  - `program_catalog`
- `mode`：
  - `append`：追加到自动发现结果
  - `replace`：替换当前院系已有候选入口
- `notes`：备注

推荐维护原则：

- 如果院系官网不对，补 `department_site`
- 如果教师页没找到，补 `teacher_hub` 或 `teacher_group`
- 如果专业页没找到，补 `program_catalog`
- 如果目标是“补老师”，不要只补学院首页；优先补明确的教师列表页

推荐策略：

- 正常情况不预配所有老师 URL
- 先跑自动发现
- 某个院系异常时，再补一条高价值 URL

## 标准流程

推荐按这个标准化顺序使用：

```text
1. 跑学校主流程
2. 自动得到院系列表和院系 Markdown
3. 检查异常院系
4. 优先修院系官网入口
5. 再补教师入口或专业入口
6. 只重跑该院系
7. 重建 Markdown
```

说明：

- 第一轮目标是先把“学校 -> 院系 -> 初版结果”跑出来。
- 第二轮开始才进入“单院系修复”。
- 不建议一开始就人工预配所有教师 URL。

### 1. 首次跑一所学校

步骤 1：配置学校入口

编辑：

```text
configs/school_pipeline_sources.csv
```

步骤 2：运行主流程

```bash
source .venv/bin/activate
CRAWL4_AI_BASE_DIRECTORY="$(pwd)" \
python scripts/production_school_pipeline.py \
  --school 清华大学 \
  --crawler-engine crawl4ai_docker \
  --enable-ai \
  --teacher-pages-per-department 3 \
  --teacher-workers 4
```

步骤 3：查看结果

重点看：

```text
output/school_finals/tsinghua_final/departments.csv
output/school_finals/tsinghua_final/teachers.csv
departments/清华大学/README.md
departments/清华大学/*.md
```

先重点检查：

- 院系是否都被发现了
- 每个院系 Markdown 顶部的 `院系官网` 是否正确
- `教师来源`、`专业来源` 是否合理

### 2. 判断哪个院系异常

优先看每个院系 Markdown：

```text
departments/{学校}/{院系}.md
```

典型异常包括：

- `教师记录：0`
- 教师来源页明显不对
- 明明有老师页，但没抽出来
- 抽出了栏目词、分页、研究所名，而不是真实老师

### 3. 补单院系高价值 URL

当某个院系异常时，不要整校重配。

直接把高价值 URL 追加到：

```text
configs/department_overrides.csv
```

例如清华大学车辆与运载学院：

```csv
清华大学,车辆与运载学院,https://www.svm.tsinghua.edu.cn/column/26_1.html,teacher_hub,append,人工补充教师入口
```

例如院系官网修正：

```csv
清华大学,安全科学学院,https://www.ses.tsinghua.edu.cn/,department_site,replace,人工修正院系官网
```

例如教师页补充：

```csv
清华大学,安全科学学院,https://www.ssafs.tsinghua.edu.cn/szdw/zgj.htm,teacher_group,append,人工确认专职教师页
```

例如专业页补充：

```csv
清华大学,安全科学学院,https://www.ses.tsinghua.edu.cn/yjsjy.htm,program_catalog,append,人工补充专业入口
```

判断原则：

- `department_site` 只用于修正院系官网
- `teacher_hub` 用于“师资队伍/教师目录”入口页，后面还要继续跟进详情页
- `teacher_group` 用于已经明确是教师列表页的页面
- 如果你的目标是补老师，而你手里已经有教师列表页，就优先用 `teacher_group`

### 4. 只重跑单个院系

示例：

```bash
SCHOOL_PIPELINE_CRAWLER_ENGINE=crawl4ai_docker \
.venv/bin/python pipeline_internal/discover_department_teachers.py \
  --school 清华大学 \
  --output-dir output/school_finals/tsinghua_final \
  --only-department 车辆与运载学院 \
  --teacher-pages-per-department 6 \
  --workers 1 \
  --enable-ai \
  --overrides configs/department_overrides.csv
```

说明：

- `--only-department`：只处理一个院系
- `--teacher-pages-per-department`：该院系最多分析多少个教师候选页
- `--workers 1`：单院系补跑时通常用 1 就够
- `--overrides`：读取人工补丁入口
- `SCHOOL_PIPELINE_CRAWLER_ENGINE=crawl4ai_docker`：强制走 Docker Crawl4AI，不走本机浏览器

如果你补的是老师，标准顺序是：

```text
1. 在 department_overrides.csv 追加 teacher_hub 或 teacher_group
2. 用 --only-department 只重跑该院系教师发现
3. 重建 departments Markdown
```

### 5. 重建院系 Markdown

```bash
.venv/bin/python pipeline_internal/build_department_markdown_tree.py \
  --school 清华大学 \
  output/school_finals/tsinghua_final \
  --root departments \
  --max-programs 500 \
  --max-teachers 500 \
  --max-linked 500
```

说明：

- `--max-programs`
- `--max-teachers`
- `--max-linked`

这 3 个参数只影响 Markdown 展示上限，不影响前面实际抓取多少条。

## 具体案例

### 清华大学 - 车辆与运载学院

异常现象：

- 自动发现到了教师相关页
- 但最初 `teachers=0`

人工补救：

```csv
清华大学,车辆与运载学院,https://www.svm.tsinghua.edu.cn/column/26_1.html,teacher_hub,append,人工补充教师入口
```

补跑命令：

```bash
.venv/bin/python pipeline_internal/discover_department_teachers.py \
  --school 清华大学 \
  --output-dir output/school_finals/tsinghua_final \
  --only-department 车辆与运载学院 \
  --teacher-pages-per-department 6 \
  --workers 1 \
  --enable-ai \
  --overrides configs/department_overrides.csv
```

结果：

- 从 `column/26_1.html` 中抽到 `78` 条教师
- 已写入 `teachers.csv`
- 已更新 `departments/清华大学/车辆与运载学院.md`

### 清华大学 - 安全科学学院

异常现象：

- 只给学院首页 `https://www.ses.tsinghua.edu.cn/` 时，不一定能稳定补出教师
- 目标是补老师时，学院首页不是高价值入口

人工补救：

```csv
清华大学,安全科学学院,https://www.ssafs.tsinghua.edu.cn/szdw/zgj.htm,teacher_group,append,人工确认专职教师页
```

补跑命令：

```bash
SCHOOL_PIPELINE_CRAWLER_ENGINE=crawl4ai_docker \
.venv/bin/python pipeline_internal/discover_department_teachers.py \
  --school 清华大学 \
  --output-dir output/school_finals/tsinghua_final \
  --only-department 安全科学学院 \
  --teacher-pages-per-department 6 \
  --workers 1 \
  --enable-ai \
  --overrides configs/department_overrides.csv
```

补跑完成后，重建 Markdown：

```bash
.venv/bin/python pipeline_internal/build_department_markdown_tree.py \
  --school 清华大学 \
  output/school_finals/tsinghua_final \
  --root departments \
  --max-programs 500 \
  --max-teachers 500 \
  --max-linked 500
```

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
departments/README.md                 # 学校级目录索引
```

## 当前已有结果

```text
departments/北京大学/
departments/北京服装学院/
departments/北京印刷学院/
departments/北京建筑大学/
departments/北京工业大学/
departments/北京理工大学/
departments/北京交通大学/
departments/北京化工大学/
departments/北京工商大学/
departments/北京邮电大学/
departments/北京石油化工学院/
departments/北京科技大学/
departments/北京航空航天大学/
departments/北方工业大学/
departments/清华大学/
departments/中国人民大学/
```

## 院校网页抓取目录（学院官网首页）

```text
ustb_colleges_pages_20260430/                       # 北京科技大学学院站点抓取
  北京科技大学_学院页面抓取文档_20260430.md
  ustb_colleges_pages_status.tsv
  html/

ncut_colleges_pages_20260430/                       # 北方工业大学学院站点抓取
  北方工业大学_学院页面抓取文档_20260430.md
  ncut_colleges_pages_status.tsv
  html/

bift_colleges_pages_20260430/                       # 北京服装学院学院站点抓取
  北京服装学院_学院页面抓取文档_20260430.md
  bift_colleges_pages_status.tsv
  html/

bupt_colleges_pages_20260430/                       # 北京邮电大学学院站点抓取
  北京邮电大学_学院页面抓取文档_20260430.md
  bupt_colleges_pages_status.tsv
  html/
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
