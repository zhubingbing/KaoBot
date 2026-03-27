# KaoBot 项目任务清单

> 更新日期: 2026-03-27
> 原则: **先验证核心方案 → 再搭框架 → 最后做功能**

---

## Phase 0: POC 验证（最高优先级）

目标: 用最小代码量验证 PageIndex 作为知识库核心的可行性

### 0.1 PageIndex 环境搭建
- [ ] 注册 PageIndex 账号，获取 API Key
- [ ] 安装 PageIndex Python SDK (`pip install pageindex`)
- [ ] 阅读 PageIndex 官方文档，理解 API 接口
- [ ] 编写 `.env.example` 模板

### 0.2 PageIndex 基础功能验证
- [ ] 编写 POC 脚本 `poc/test_pageindex_basic.py`
- [ ] 测试文档上传: 上传一份导师信息 PDF，确认返回 doc_id
- [ ] 测试索引状态轮询: 等待索引完成
- [ ] 测试基础查询: 对已索引文档提问，验证回答质量
- [ ] 测试来源引用: 确认返回的答案包含页码/段落引用

### 0.3 PageIndex 准确率评测
- [ ] 准备 3-5 份真实导师信息文档（PDF格式）
- [ ] 设计 20 道评测题目（覆盖: 姓名/方向/招生/论文/联系方式）
- [ ] 编写评测脚本 `poc/eval_accuracy.py`
- [ ] 运行评测，记录准确率
- [ ] 如果准确率 < 90%，分析失败 case，调整 prompt/文档格式
- [ ] 输出评测报告 `poc/eval_report.md`

### 0.4 PageIndex MCP 验证
- [ ] 安装 PageIndex MCP Server (`pageindex-mcp`)
- [ ] 配置 MCP Server 连接
- [ ] 测试通过 MCP 协议调用 PageIndex 查询
- [ ] 验证 MCP 模式 vs 直接 API 模式的差异

### 0.5 LLM Function Calling 验证
- [ ] 本地安装 Ollama + Qwen3:8B 模型
- [ ] 编写 POC 脚本 `poc/test_function_calling.py`
- [ ] 测试基础对话能力
- [ ] 测试 Function Calling: 定义一个 dummy tool，验证模型能正确返回 tool_calls
- [ ] 测试中文场景下的 Function Calling 稳定性（10次调用，统计成功率）
- [ ] 如果 Qwen3 不稳定，测试 DeepSeek API 作为备选

### 0.6 端到端 POC
- [ ] 编写 `poc/e2e_demo.py`: 用户提问 → LLM 决定调用 PageIndex → 返回答案
- [ ] 手动测试 5 个典型问题
- [ ] 记录延迟（每次查询耗时）
- [ ] 输出 POC 结论文档 `poc/conclusion.md`（可行/不可行/需调整）

---

## Phase 1: 项目基础设施

### 1.1 项目初始化
- [ ] 创建 `pyproject.toml`（项目元数据、依赖、入口点）
- [ ] 创建目录结构（src/kaobot/、config/、tests/、data/）
- [ ] 创建所有 `__init__.py` 文件
- [ ] 创建 `.gitignore`（排除 .env、data/cache/、data/indexes/、__pycache__）
- [ ] 创建 `.env.example`
- [ ] 创建 `config/default.yaml`（默认配置）
- [ ] 创建 `config/models.yaml`（LLM 提供者配置模板）

### 1.2 配置系统
- [ ] 实现 `src/kaobot/utils/config.py`
  - [ ] KaoBotConfig (pydantic-settings)
  - [ ] 三层配置: env > ~/.kaobot/config.yaml > config/default.yaml
  - [ ] 支持 `KAOBOT_` 前缀环境变量
- [ ] 实现 `src/kaobot/utils/logging.py`（structured logging with rich）
- [ ] 编写 `tests/unit/test_config.py`

### 1.3 数据模型
- [ ] 实现 `src/kaobot/data/models.py`
  - [ ] AcceptingStatus 枚举
  - [ ] Publication 模型
  - [ ] ResearchDirection 模型
  - [ ] ContactInfo 模型
  - [ ] TutorInfo 模型（核心）
  - [ ] ToolCall / ToolResult 模型
  - [ ] AgentMessage 模型
  - [ ] ReActStep 模型
- [ ] 编写 `tests/unit/test_models.py`（序列化/反序列化/校验）

---

## Phase 2: Agent 核心框架

### 2.1 LLM 客户端
- [ ] 实现 `src/kaobot/llm/base.py`（LLMClient ABC + ChatResponse 模型）
- [ ] 实现 `src/kaobot/llm/openai_compat.py`（OpenAICompatClient）
  - [ ] chat() 方法，支持 messages + tools 参数
  - [ ] 处理 tool_calls 响应解析
  - [ ] 流式输出支持（stream=True）
  - [ ] 错误处理（连接失败、超时、模型不存在）
- [ ] 实现 `src/kaobot/llm/function_call.py`（Hermes 格式解析辅助）
- [ ] 编写 `tests/unit/test_llm_client.py`（mock 测试）

### 2.2 工具系统
- [ ] 实现 `src/kaobot/tools/base.py`
  - [ ] BaseTool ABC（name, description, get_parameters_schema, execute）
  - [ ] to_openai_tool() 方法（转换为 function calling 格式）
- [ ] 实现 `src/kaobot/core/tool_registry.py`
  - [ ] register(tool) / unregister(name)
  - [ ] get_schemas() → list[dict]
  - [ ] execute(tool_call) → ToolResult
- [ ] 编写 `tests/unit/test_tool_registry.py`

### 2.3 ReAct Brain
- [ ] 实现 `src/kaobot/core/brain.py`
  - [ ] run(query, tools, system_prompt, memory_context) → str
  - [ ] ReAct 循环: LLM推理 → 检测tool_calls → 执行工具 → 拼接结果 → 继续/终止
  - [ ] 最大迭代次数控制（默认10）
  - [ ] 每步记录 ReActStep（用于调试和展示）
  - [ ] 异常处理: 工具执行失败时将错误信息反馈给 LLM
- [ ] 实现 `src/kaobot/core/prompt.py`（系统提示词模板组装）
- [ ] 编写 `tests/unit/test_brain.py`（mock LLM，验证循环逻辑）

### 2.4 记忆系统
- [ ] 实现 `src/kaobot/core/memory.py`
  - [ ] ShortTermMemory: 对话历史（list[AgentMessage]，内存中）
  - [ ] LongTermMemory: ~/.kaobot/memory/ 下 Markdown 文件
    - [ ] preferences.md（目标院校、研究方向偏好）
    - [ ] search_history.md（查过哪些导师）
  - [ ] get_context() → str（拼接为系统提示词的一部分）
  - [ ] update(key, value) / load() / save()
- [ ] 编写 `tests/unit/test_memory.py`

---

## Phase 3: 知识库与工具实现

### 3.1 PageIndex 集成（正式版）
- [ ] 实现 `src/kaobot/rag/pageindex_client.py`
  - [ ] 封装 PageIndex SDK: upload_document, check_status, query
  - [ ] 自动重试 + 状态轮询
  - [ ] 本地维护 doc_id 映射（SQLite）
- [ ] 实现 `src/kaobot/rag/document_ingest.py`
  - [ ] 支持 PDF、Word、纯文本上传
  - [ ] 文件去重（hash 检查）
  - [ ] 索引状态管理
- [ ] 实现 `src/kaobot/tools/pageindex_query.py`（作为 Agent Tool）
  - [ ] 参数: query(str), doc_ids(optional)
  - [ ] 返回: 答案 + 来源引用
- [ ] 编写 `tests/integration/test_pageindex.py`

### 3.2 数据存储层
- [ ] 实现 `src/kaobot/data/store.py`
  - [ ] SQLite 初始化（自动建表）
  - [ ] 表: documents（doc_id, file_path, file_hash, status, created_at）
  - [ ] 表: tutors（缓存结构化导师数据）
  - [ ] 表: query_cache（查询缓存，避免重复调用）
  - [ ] CRUD 方法
- [ ] 编写 `tests/unit/test_store.py`

### 3.3 论文检索工具
- [ ] 实现 `src/kaobot/tools/scholar_search.py`
  - [ ] Semantic Scholar API 客户端
    - [ ] 按作者名搜索 → 获取 author_id
    - [ ] 按 author_id 获取论文列表
    - [ ] 返回 list[Publication]
  - [ ] 限速处理（100 requests / 5 min）
  - [ ] 降级: scholarly 库（Google Scholar）
- [ ] 编写 `tests/unit/test_scholar.py`（mock API 响应）

### 3.4 本地数据查询工具
- [ ] 实现 `src/kaobot/tools/data_lookup.py`
  - [ ] 查询本地缓存的导师信息
  - [ ] 按姓名/院校/方向模糊搜索
- [ ] 编写 `tests/unit/test_data_lookup.py`

---

## Phase 4: TutorAgent + Gateway

### 4.1 TutorAgent
- [ ] 实现 `src/kaobot/agents/base.py`（BaseAgent ABC）
  - [ ] name, description, system_prompt
  - [ ] get_tools() → list[BaseTool]
- [ ] 实现 `src/kaobot/agents/tutor.py`
  - [ ] 注册工具: pageindex_query, scholar_search, data_lookup
  - [ ] 系统提示词: 角色定义 + 输出格式要求 + 工具使用指南
  - [ ] 后处理: 将原始 LLM 输出格式化为导师信息卡片
- [ ] 编写 `tests/unit/test_tutor_agent.py`

### 4.2 Gateway 路由
- [ ] 实现 `src/kaobot/core/gateway.py`
  - [ ] 意图分类（关键词匹配，Phase 1 够用）
  - [ ] 路由到对应 Agent
  - [ ] 统一的请求/响应流程
- [ ] 编写 `tests/unit/test_gateway.py`

---

## Phase 5: CLI 交互层

### 5.1 基础 CLI
- [ ] 实现 `src/kaobot/cli.py`
  - [ ] `kaobot chat` — 交互式聊天模式
  - [ ] `kaobot ingest <file>` — 上传文档到 PageIndex
  - [ ] `kaobot ingest --list` — 查看已索引文档
  - [ ] `kaobot tutor search <query>` — 直接搜索导师
  - [ ] `kaobot config set <key> <value>` — 设置配置
  - [ ] `kaobot config show` — 显示当前配置
- [ ] 交互式聊天模式
  - [ ] prompt_toolkit 输入（历史记录、自动补全）
  - [ ] rich 格式化输出（思考过程灰色、工具调用蓝色、最终答案白色）
  - [ ] 流式输出（逐字显示 LLM 回答）
- [ ] 编写 `tests/unit/test_cli.py`（click.testing.CliRunner）

### 5.2 用户体验优化
- [ ] 首次运行引导: 检测 Ollama/配置，提示设置
- [ ] 进度提示: 文档索引、查询中的等待状态
- [ ] 错误信息友好化: LLM 离线、PageIndex 失败等

---

## Phase 6: 端到端测试与收尾

### 6.1 集成测试
- [ ] 编写 `tests/integration/test_e2e.py`
  - [ ] 上传文档 → 查询导师 → 验证结果
  - [ ] 多轮对话 → 验证上下文保持
  - [ ] 论文检索 → 验证 Semantic Scholar 集成
- [ ] 人工评测: 20 道题目准确率测试

### 6.2 项目收尾
- [ ] 错误处理全面检查
- [ ] .gitignore 完善
- [ ] README.md（安装、配置、使用说明）
- [ ] 依赖锁定（pip freeze 或 uv lock）

---

## Backlog（Phase 2+ TODO）

### 爬虫系统（数据自动采集）
- [ ] BaseScraper + 令牌桶限速
- [ ] LLM 辅助的通用教师页面解析器
- [ ] 大学专用解析器（清华/北大/浙大等）
- [ ] 招生状态自动检测（关键词匹配 + LLM 判断）
- [ ] 定时更新机制

### 真题库（F2+F3）
- [ ] 真题文档上传与索引
- [ ] 按院校/年份/科目检索
- [ ] ExamAgent 实现

### 复试辅导（F4+F5）
- [ ] 简历模板系统
- [ ] ResumeAgent: 根据导师方向定制简历
- [ ] InterviewAgent: 中英文自我介绍生成
- [ ] 模拟面试对话

### 院校调剂（F6）
- [ ] 调剂信息采集
- [ ] AdvisorAgent: 智能择校推荐
- [ ] 分数线分析

### Web UI（Phase 2）
- [ ] FastAPI 后端
- [ ] React 前端
- [ ] 用户系统
- [ ] 文档管理界面
