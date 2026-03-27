"""
端到端 POC Demo
================
验证完整链路: 用户提问 → LLM 选择工具 → 调用 PageIndex → 返回答案

使用方法:
  1. 确保 .env 中设置了 PAGEINDEX_API_KEY
  2. 确保 Ollama 运行中 (ollama serve) 且已拉取模型
  3. 先上传文档: python poc/test_pageindex_basic.py --pdf <文件>
  4. 运行: python poc/e2e_demo.py --doc-id <doc_id>
"""

import argparse
import json
import os
import sys
import time
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ─── 配置 ───
LLM_BASE_URL = os.getenv("KAOBOT_LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.getenv("KAOBOT_LLM_MODEL", "qwen3:8b")
LLM_API_KEY = os.getenv("KAOBOT_LLM_API_KEY", "ollama")
PAGEINDEX_API_KEY = os.getenv("PAGEINDEX_API_KEY", "")

# ─── 工具定义 ───
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_base",
            "description": "在知识库中查询导师信息。适用于查询导师的研究方向、招生状态、联系方式等信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查询的问题，用自然语言描述",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

SYSTEM_PROMPT = """你是 KaoBot，一个专业的考研辅导助手。你的主要职责是帮助考研学生查询导师信息。

你可以使用以下工具:
- query_knowledge_base: 在知识库中查询导师的研究方向、招生状态、联系方式等信息

工作流程:
1. 理解用户的问题
2. 使用 query_knowledge_base 工具查询知识库获取信息
3. 基于查询结果，用清晰的格式回答用户

回答要求:
- 信息必须基于知识库查询结果，不要编造
- 如果知识库中没有相关信息，明确告知用户
- 对导师信息使用"导师信息卡片"格式展示
- 标注信息来源"""


def create_pageindex_tool(doc_id: str):
    """创建 PageIndex 查询函数"""
    from pageindex import PageIndexClient
    pi_client = PageIndexClient(api_key=PAGEINDEX_API_KEY)

    def query_knowledge_base(query: str) -> str:
        """调用 PageIndex 查询知识库"""
        response = pi_client.chat_completions(
            messages=[{"role": "user", "content": query}],
            doc_id=doc_id,
        )
        return response["choices"][0]["message"]["content"]

    return query_knowledge_base


def react_loop(
    llm_client: OpenAI,
    query: str,
    tool_functions: dict,
    max_steps: int = 5,
) -> str:
    """
    ReAct 循环: Think → Act → Observe → 重复直到最终回答

    返回最终回答文本
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    for step in range(max_steps):
        print(f"\n  --- ReAct Step {step + 1} ---")

        start = time.time()
        response = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=TOOLS,
            temperature=0.1,
        )
        elapsed = round(time.time() - start, 2)
        msg = response.choices[0].message

        # 如果有文本内容 (思考/最终回答)
        if msg.content:
            print(f"  [Think] ({elapsed}s): {msg.content[:200]}...")

        # 检查是否调用工具
        if msg.tool_calls:
            messages.append(msg)

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments)
                print(f"  [Act] 调用 {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})")

                # 执行工具
                if tool_name in tool_functions:
                    try:
                        tool_start = time.time()
                        result = tool_functions[tool_name](**tool_args)
                        tool_elapsed = round(time.time() - tool_start, 2)
                        print(f"  [Observe] ({tool_elapsed}s) 结果: {result[:150]}...")
                    except Exception as e:
                        result = f"工具调用失败: {e}"
                        print(f"  [Observe] ERROR: {result}")
                else:
                    result = f"未知工具: {tool_name}"
                    print(f"  [Observe] {result}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            # 无工具调用 = 最终回答
            print(f"  [Final] ({elapsed}s)")
            return msg.content or "(空回答)"

    return "(达到最大步数限制，未能给出最终回答)"


def interactive_mode(llm_client: OpenAI, doc_id: str):
    """交互式对话模式"""
    tool_functions = {
        "query_knowledge_base": create_pageindex_tool(doc_id),
    }

    print(f"\n{'='*60}")
    print(f"  KaoBot E2E Demo - 交互模式")
    print(f"  输入问题开始对话，输入 'quit' 退出")
    print(f"{'='*60}\n")

    while True:
        try:
            query = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("再见！")
            break

        total_start = time.time()
        answer = react_loop(llm_client, query, tool_functions)
        total_elapsed = round(time.time() - total_start, 2)

        print(f"\n  {'─'*50}")
        print(f"  KaoBot ({total_elapsed}s):")
        for line in answer.split("\n"):
            print(f"  {line}")
        print(f"  {'─'*50}\n")


def batch_test(llm_client: OpenAI, doc_id: str):
    """批量测试模式"""
    tool_functions = {
        "query_knowledge_base": create_pageindex_tool(doc_id),
    }

    test_queries = [
        "这份文档中有哪些导师？",
        "请告诉我文档中某位导师的研究方向",
        "有哪些导师今年在招收研究生？",
        "帮我对比一下文档中两位导师的研究方向",
        "如果我对机器学习感兴趣，你推荐哪位导师？",
    ]

    print(f"\n批量测试 ({len(test_queries)} 个问题):\n")

    for i, query in enumerate(test_queries, 1):
        print(f"{'='*60}")
        print(f"问题 {i}: {query}")

        start = time.time()
        answer = react_loop(llm_client, query, tool_functions)
        elapsed = round(time.time() - start, 2)

        print(f"\n回答 ({elapsed}s):")
        for line in answer.split("\n"):
            print(f"  {line}")
        print()


def main():
    parser = argparse.ArgumentParser(description="KaoBot E2E POC Demo")
    parser.add_argument("--doc-id", required=True, help="PageIndex 文档 ID")
    parser.add_argument("--batch", action="store_true", help="批量测试模式（非交互）")
    args = parser.parse_args()

    print("=" * 60)
    print("  KaoBot E2E POC - PageIndex + LLM Agent")
    print(f"  LLM: {LLM_MODEL} @ {LLM_BASE_URL}")
    print(f"  doc_id: {args.doc_id}")
    print("=" * 60)

    # 检查配置
    if not PAGEINDEX_API_KEY or PAGEINDEX_API_KEY == "your_pageindex_api_key_here":
        print("[ERROR] 请在 .env 中设置 PAGEINDEX_API_KEY")
        sys.exit(1)

    llm_client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

    # 测试 LLM 连接
    try:
        llm_client.models.list()
        print(f"[OK] LLM 服务连接成功")
    except Exception as e:
        print(f"[ERROR] 无法连接 LLM: {e}")
        sys.exit(1)

    # 测试 PageIndex 连接
    try:
        from pageindex import PageIndexClient
        pi = PageIndexClient(api_key=PAGEINDEX_API_KEY)
        doc_info = pi.get_document(args.doc_id)
        print(f"[OK] PageIndex 文档状态: {doc_info.get('status', 'unknown')}")
        if doc_info.get("status") != "completed":
            print(f"[WARN] 文档未就绪，查询可能失败")
    except Exception as e:
        print(f"[ERROR] PageIndex 连接失败: {e}")
        sys.exit(1)

    if args.batch:
        batch_test(llm_client, args.doc_id)
    else:
        interactive_mode(llm_client, args.doc_id)


if __name__ == "__main__":
    main()
