# 高校官网采集架构设计

## 1. 目标

面向几百所高校，建立一条可扩展、可维护、尽量少定制的采集链路，输出统一结果：

```text
学校 -> 院系 -> 专业/项目 -> 教师池
```

当前重点不是“全站网页备份”，而是“结构化信息生产”：

- 学校官网与高价值入口
- 院系/机构入口
- 本科/硕士/博士专业或项目
- 院系教师池
- 来源 URL 与可复核 Markdown

## 2. 结论

最合适的方案不是这两种极端：

- 不是“每个学校单独写脚本/写规则”
- 也不是“所有页面都丢给 LLM 盲抽”

更合适的生产架构是：

```text
学校入口配置 + Crawl4AI 抓取 + LLM 页面理解 + 通用动作流转 + 规则清洗兜底
```

一句话概括：

- Crawl4AI 负责“拿到页面”
- LLM 负责“理解页面是什么、里面有什么”
- Pipeline 负责“下一步该跟哪类链接、该落什么表”
- 规则负责“去重、归一化、过滤脏项”

## 3. 为什么这套更合适

### 3.1 比纯规则更准

高校网站结构差异很大，但页面语义相对稳定，经常反复出现这些类型：

- 院系列表页
- 教师入口页
- 教师分组页
- 教师详情页
- 专业目录页
- 招生页

规则擅长识别 URL 和关键词，但不擅长稳健理解层级关系。  
LLM 更适合判断：

- 当前页是不是教师页
- 当前页是入口页还是分组页还是详情页
- 页面里的“研究所名”“分页”“栏目标题”是不是老师

### 3.2 比纯 LLM 更稳

如果把所有页面都交给 LLM：

- 成本高
- 速度慢
- 输出波动大
- 很难做大规模并发

所以 LLM 不应该负责全站盲跑。它只处理已经被筛选出来的高价值页。

### 3.3 比每校定制更可扩展

几百所大学不可能维护几百套页面规则。  
真正能复用的是“页面类型”和“动作流转”：

- `department_index` -> 抽院系列表
- `teacher_hub_page` -> 继续找教师子页
- `teacher_group_page` -> 抽教师列表
- `teacher_profile_page` -> 抽单个老师详情
- `program_catalog_page` -> 抽专业/项目

## 4. 总体架构

```text
school_pipeline_sources.csv
  -> 选定学校高价值入口
  -> Crawl4AI 抓取页面
  -> 候选链接发现
  -> LLM 分类页面类型
  -> 按页面类型执行通用动作
  -> 结构化落库
  -> departments/{学校}/{院系}.md
```

具体分层如下：

### 4.1 入口配置层

作用：只告诉系统“从哪里开始更有效”，而不是描述全站规则。

当前建议字段：

```text
school_name
official_site_url
entry_url
entry_type
crawl_mode
notes
```

例如：

- 清华大学 -> `https://www.tsinghua.edu.cn/yxsz.htm`
- 北京航空航天大学 -> `https://faculty.buaa.edu.cn/`

这层配置是必要的，因为很多高校首页并不是最好的院系入口。

### 4.2 抓取层：Crawl4AI

作用：

- 抓 HTML
- 处理动态页面
- 生成 clean markdown / fit markdown
- 提取链接
- 保留必要脚本文本

这层只负责“把页面拿到手”，不负责最终语义判断。

### 4.3 理解层：LLM

作用：

- 判断页面类型
- 理解页面层级
- 抽取教师/专业等结构化信息

这层负责回答：

- 这是院系列表页吗
- 这是教师入口页吗
- 这是研究所级别的教师分组页吗
- 页面里哪些是老师，哪些只是栏目词

### 4.4 动作层：通用状态机

作用：根据页面类型决定下一步怎么做。

示意：

```text
department_index
  -> 抽院系 URL

teacher_hub_page
  -> 继续跟进研究所/教研室/教师分组页

teacher_group_page
  -> 抽教师列表
  -> 可选继续跟进教师详情页

teacher_profile_page
  -> 抽单个教师详情

program_catalog_page
  -> 抽专业/项目
```

这层是可复用的，不应该按学校写死。

### 4.5 清洗落库层

作用：

- URL 归一化
- 去重
- 栏目词过滤
- 分页词过滤
- 院系归并
- 来源 URL 保留

这层负责稳定性，不负责页面理解。

## 5. 页面类型设计

建议至少定义这些页面类型：

```text
seed_url
department_index
department_site
teacher_hub_page
teacher_group_page
teacher_profile_page
program_catalog_page
admission_page
news_page
other
```

### 5.1 `department_index`

典型特征：

- 院系设置
- 学部与院系
- 组织机构
- 学院导航

动作：

- 抽取院系名与院系站点 URL

### 5.2 `teacher_hub_page`

典型特征：

- 标题或导航中有 `师资队伍 / 教师 / 导师`
- 页面主要是研究所、教研室、教师类别入口
- 不是具体老师列表

例子：

- `https://www.civil.tsinghua.edu.cn/ce/szdw/jiaosh.htm`

动作：

- 跟进子分组页
- 不把“研究所名”当老师

### 5.3 `teacher_group_page`

典型特征：

- 页面标题是某研究所、教研室、团队
- 页面中列出多位老师
- 每位老师往往带职称、电话、详情页链接

例子：

- `https://www.civil.tsinghua.edu.cn/ce/szdw/jiaosh/fzjzgcyjs.htm`

动作：

- 抽教师列表
- 可记录 `sub_unit`

### 5.4 `teacher_profile_page`

典型特征：

- 只有一个老师
- 有详细简介、研究方向、邮箱、教育经历等

动作：

- 抽单个老师详情
- 与教师池合并

### 5.5 `program_catalog_page`

典型特征：

- 本科专业页
- 硕士/博士专业目录
- 招生项目页
- 培养方案页

动作：

- 抽专业/项目结构

## 6. 数据模型

### 6.1 `departments.csv`

```text
school_name
department
division
site_url
source_url
confidence
extract_method
```

### 6.2 `teachers.csv`

建议主字段：

```text
school_name
department
sub_unit
teacher_name
title
research_fields
teacher_unit
email
teacher_profile_url
source_url
confidence
extract_method
```

说明：

- `department` 是最终归属院系
- `sub_unit` 用于记录 `研究所/教研室/团队`
- `source_url` 必须保留，便于复核

### 6.3 `unified_programs.csv`

```text
school_name
degree_level
department
major_code
major_name
research_direction
study_mode
source_url
```

## 7. LLM 的职责边界

LLM 负责：

- 页面类型判断
- 教师/专业结构化抽取
- 识别噪声与栏目词
- 理解层级关系

LLM 不负责：

- 大规模链接遍历调度
- URL 去重
- CSV 合并
- 文件写盘
- 增量更新
- 并发控制

这是生产稳定性的关键边界。

## 8. 配置设计

### 8.1 必需配置：学校入口 CSV

文件：

```text
configs/school_pipeline_sources.csv
```

作用：

- 每个学校配置一个高价值入口
- 由人工维护少量信息
- 不写复杂页面规则

推荐维护策略：

- 必填：
  - `school_home`
  - `department_index`
- 选填：
  - `teacher_hub`
  - `teacher_group`
  - `program_catalog`

也就是说，正常情况下人工只需要提供：

- 学校主站 URL
- 院系入口 URL

如果系统对某个院系抽取异常，再补具体的教师页 URL。

### 8.2 可选配置：页面类型动作表

建议后续增加：

```text
configs/page_type_actions.csv
```

示例：

```csv
page_type,follow_links,extract_mode,next_level
department_index,true,department_links,department
teacher_hub_page,true,group_links,teacher_group
teacher_group_page,true,teacher_list,teacher_profile
teacher_profile_page,false,teacher_profile,none
program_catalog_page,false,program_table,none
```

这个配置描述的是“页面类型的通用处理动作”，不是“某所学校怎么写死”。

### 8.3 可选配置：院系补丁配置

建议后续增加：

```text
configs/department_overrides.csv
```

作用：

- 只对异常院系补充入口
- 不影响整校主流程
- 不要求人工提前维护所有教师页

建议字段：

```csv
school_name,department,url,url_type,mode,notes
```

示例：

```csv
school_name,department,url,url_type,mode,notes
清华大学,安全科学学院,https://www.ses.tsinghua.edu.cn/,department_site,append,院系官网补充
清华大学,安全科学学院,https://www.ses.tsinghua.edu.cn/szdw.htm,teacher_hub,append,人工确认教师入口
清华大学,电子工程系,https://www.ee.tsinghua.edu.cn/ryqk/teacher/xxgdzyjs/js2.htm,teacher_group,append,人工确认在职教师页
```

字段含义：

- `school_name`：学校名
- `department`：院系名
- `url`：补充入口
- `url_type`：
  - `department_site`
  - `teacher_hub`
  - `teacher_group`
  - `program_catalog`
- `mode`：
  - `append`：追加到现有候选入口
  - `replace`：替换该院系原有入口
- `notes`：备注

推荐维护原则：

- 如果院系官网不对，补 `department_site`
- 如果教师页没找到，补 `teacher_hub` 或 `teacher_group`
- 如果专业页没找到，补 `program_catalog`

这样一个院系的人工补丁信息都集中在同一个文件里。

## 9. 典型流程

### 9.1 清华大学土木工程系

```text
清华大学院系列表页
  -> 土木工程系站点
  -> 师资队伍/教师总页 jiaosh.htm
  -> 研究所分组页 fzjzgcyjs.htm
  -> 具体教师详情页
```

这里 `jiaosh.htm` 应识别为：

- `teacher_hub_page`

而 `fzjzgcyjs.htm` 应识别为：

- `teacher_group_page`

### 9.2 清华大学电子工程系

```text
人员情况 -> 在职教师 -> 页面内嵌 JS 数据 qh_data
```

这里老师不在普通 DOM 列表里，而在脚本里。  
LLM 需要结合：

- 页面正文
- 锚文本
- script 片段

一起判断和抽取。

## 10. 人机协作方式

推荐的人机分工如下：

### 10.1 人工负责什么

人工最适合提供“高价值入口 URL”，不适合去描述 DOM 结构或脚本细节。

正常情况下只需要提供：

- 学校主站 URL
- 院系入口 URL

异常情况下再补：

- 某个院系官网 URL
- 某个更高价值的教师入口 URL
- 某个专业目录 URL

### 10.2 系统负责什么

系统自动完成：

- 从院系入口页发现院系 URL
- 进入院系官网分析教师页和专业页
- 用 LLM 判断页面类型
- 抽取教师和专业
- 生成 Markdown 和 CSV

### 10.3 为什么不建议人工预配所有教师 URL

因为一所大学院系很多，教师页入口又经常变化：

- 人工一次性配全成本太高
- 预配过多入口很容易过期
- 更适合的方式是：
  - 先跑自动发现
  - 异常再补单个院系 URL

## 11. 异常补救机制

### 11.1 什么时候需要人工补

典型信号：

- 某院系 `教师记录=0`
- 抽到了栏目词，没有抽到真实老师
- 抽到了错误院系的老师
- Markdown 里 `院系官网` 或 `教师来源` 明显不对

### 11.2 人工怎么补

建议按“单院系补救”的方式反馈，不要整校重配。

最小反馈格式：

```text
学校：清华大学
院系：安全科学学院
教师入口：https://...
备注：这个页里有老师
```

如果你知道更准确的院系站点，也可以补：

```text
学校：清华大学
院系：安全科学学院
院系官网：https://...
教师入口：https://...
```

如果专业页没有被发现，也可以直接补：

```text
学校：清华大学
院系：安全科学学院
专业入口：https://...
```

### 11.3 程序怎么处理补丁

程序读取 `department_overrides.csv` 后，只对指定院系生效：

```text
现有院系结果
  + 单院系补丁 URL
  -> 只重跑这个院系
  -> 合并写回 teachers.csv / sources.csv / markdown
```

原则：

- 不整校重跑
- 不覆盖其他院系结果
- 只更新这个院系相关数据

## 12. 操作流程

### 12.1 正常生产流程

适用于首次跑一所学校。

步骤 1：配置学校入口

在 `configs/school_pipeline_sources.csv` 里至少配置：

- `school_home`
- `department_index`

例如清华大学：

```csv
school_name,official_site_url,entry_url,entry_type,crawl_mode,notes
清华大学,https://www.tsinghua.edu.cn/,https://www.tsinghua.edu.cn/yxsz.htm,department_index,config_only,清华院系设置页；先从院系入口开始
```

步骤 2：运行主流程

```bash
.venv/bin/python scripts/production_school_pipeline.py \
  --school 清华大学 \
  --crawler-engine crawl4ai_docker \
  --enable-ai
```

步骤 3：查看输出

重点看：

- `output/school_finals/{school}_final/departments.csv`
- `output/school_finals/{school}_final/teachers.csv`
- `departments/{学校}/*.md`

### 12.2 异常院系补救流程

适用于“单个院系没抽好”。

步骤 1：定位异常院系

从 `departments/{学校}/{院系}.md` 里看：

- 院系官网是不是对的
- 教师来源是不是对的
- 结果是不是为空或明显脏

步骤 2：人工补一个更有价值的 URL

把 URL 写入：

```text
configs/department_overrides.csv
```

例如：

```csv
school_name,department,url,url_type,mode,notes
清华大学,安全科学学院,https://www.ses.tsinghua.edu.cn/szdw.htm,teacher_hub,append,人工补充教师入口
```

步骤 3：只重跑这个院系

建议命令：

```bash
.venv/bin/python pipeline_internal/discover_department_teachers.py \
  --school 清华大学 \
  --output-dir output/school_finals/tsinghua_final \
  --only-department 安全科学学院 \
  --teacher-pages-per-department 6 \
  --workers 1 \
  --enable-ai
```

步骤 4：重建这个学校的 Markdown

```bash
.venv/bin/python pipeline_internal/build_department_markdown_tree.py \
  --school 清华大学 \
  output/school_finals/tsinghua_final \
  --root departments \
  --max-programs 500 \
  --max-teachers 500 \
  --max-linked 500
```

### 12.3 未来建议支持的补救命令

后续建议主入口直接支持：

```bash
.venv/bin/python scripts/production_school_pipeline.py \
  --school 清华大学 \
  --only-department 安全科学学院 \
  --use-overrides \
  --enable-ai
```

含义：

- 只处理一个院系
- 自动读取 `department_overrides.csv`
- 自动合并写回结果

## 13. 为什么不建议“每个学校单独规则”

问题：

- 前期快
- 后期不可维护
- 每遇到一个新学校、新模板、新 JS 结构都要补规则

可以接受的例外：

- 某类通用数据源 adapter
- 某类特定入口 adapter

例如：

- `department_index` adapter
- `faculty_portal_tsites` adapter
- `admission_html_table` adapter
- `pdf_catalog` adapter

这些是“按页面/数据源类型”抽象，不是“按学校”抽象。

## 14. 当前推荐路线

短期：

1. 保持学校级入口 CSV
2. 用 Crawl4AI 抓页面
3. 把教师链路正式升级为：
   - `teacher_hub_page`
   - `teacher_group_page`
   - `teacher_profile_page`
4. LLM 负责类型判断和结构化抽取
5. 规则只做清洗与去重

中期：

1. 引入 `page_type_actions.csv`
2. 统一页面动作流转
3. 引入 `sub_unit` 字段
4. 增加 run manifest 和更新时间记录

长期：

1. 用这一套架构跑批量学校
2. 只在入口配置层人工维护
3. 尽量不新增学校级脚本

## 15. 当前仓库对应关系

当前主入口：

- [scripts/production_school_pipeline.py](/Users/zhubingbing/Desktop/Kaobot/docs/院校信息/scripts/production_school_pipeline.py)

当前学校入口配置：

- [configs/school_pipeline_sources.csv](/Users/zhubingbing/Desktop/Kaobot/docs/院校信息/configs/school_pipeline_sources.csv)

当前教师发现：

- [pipeline_internal/discover_department_teachers.py](/Users/zhubingbing/Desktop/Kaobot/docs/院校信息/pipeline_internal/discover_department_teachers.py)

当前 Markdown 生成：

- [pipeline_internal/build_department_markdown_tree.py](/Users/zhubingbing/Desktop/Kaobot/docs/院校信息/pipeline_internal/build_department_markdown_tree.py)

## 16. 一句话结论

最合适、最能扩展、也最能提高准确率的架构是：

```text
少量学校入口配置
  + Crawl4AI 抓页面
  + LLM 理解页面类型与层级
  + 通用状态机执行下一步
  + 规则清洗兜底
```

这套方案既避免“几百所学校逐个定制”，也避免“全靠 LLM 失控”。
