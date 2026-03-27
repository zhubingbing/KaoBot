"""
LLM Function Calling POC 验证
==============================
验证 Qwen3/DeepSeek 的 Function Calling 能力是否满足 Agent 需求。

使用方法:
  1. 确保 Ollama 已安装并运行: ollama serve
  2. 拉取模型: ollama pull qwen3:8b
  3. 运行: python poc/test_function_calling.py

验证项:
  - 基础对话能力
  - Function Calling: 模型能否正确返回 tool_calls
  - 中文场景 Function Calling 稳定性
  - 多工具选择能力
"""

import json
import os
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 配置
BASE_URL = os.getenv("KAOBOT_LLM_BASE_URL", "http://localhost:11434/v1")
MODEL = os.getenv("KAOBOT_LLM_MODEL", "qwen3:8b")
API_KEY = os.getenv("KAOBOT_LLM_API_KEY", "ollama")


def get_client():
    """初始化 OpenAI 兼容客户端"""
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    print(f"[INFO] LLM 配置: {BASE_URL} / {MODEL}")
    return client


# ─── 测试用的模拟工具定义 ───

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_tutor",
            "description": "搜索导师信息。根据导师姓名和/或大学名称搜索导师的详细信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "导师姓名（中文或英文）",
                    },
                    "university": {
                        "type": "string",
                        "description": "大学名称",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_publications",
            "description": "获取导师的论文列表。返回指定导师的近期学术论文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "author_name": {
                        "type": "string",
                        "description": "作者姓名",
                    },
                    "year_from": {
                        "type": "integer",
                        "description": "起始年份",
                    },
                },
                "required": ["author_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_base",
            "description": "查询知识库。在已索引的文档中搜索信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "查询问题",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# 模拟工具返回
MOCK_RESULTS = {
    "search_tutor": json.dumps(
        {
            "name": "李明",
            "university": "清华大学",
            "department": "计算机科学与技术系",
            "title": "教授/博导",
            "research_directions": ["自然语言处理", "大语言模型", "知识图谱"],
            "accepting_students": "2026年招收硕士2名",
            "email": "liming@tsinghua.edu.cn",
        },
        ensure_ascii=False,
    ),
    "get_publications": json.dumps(
        {
            "papers": [
                {"title": "Large Language Models for NLP", "venue": "ACL 2025", "citations": 120},
                {"title": "Knowledge Graph Reasoning", "venue": "NeurIPS 2024", "citations": 85},
            ]
        },
        ensure_ascii=False,
    ),
    "query_knowledge_base": json.dumps(
        {"answer": "根据文档，清华大学计算机系2026年计划招收学术型硕士30名，专业型硕士50名。"},
        ensure_ascii=False,
    ),
}


def test_basic_chat(client):
    """测试 1: 基础中文对话"""
    print(f"\n{'='*50}")
    print(f"测试 1: 基础中文对话")
    print(f"{'='*50}")

    start = time.time()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "你是一个考研辅导助手。"},
            {"role": "user", "content": "你好，请简单介绍一下你能帮我做什么？"},
        ],
        temperature=0.7,
        max_tokens=200,
    )
    elapsed = round(time.time() - start, 2)

    content = response.choices[0].message.content
    print(f"  耗时: {elapsed}s")
    print(f"  回答: {content[:300]}")
    print(f"[OK] 基础对话正常")
    return True


def test_single_tool_call(client):
    """测试 2: 单工具调用"""
    print(f"\n{'='*50}")
    print(f"测试 2: 单工具 Function Calling")
    print(f"{'='*50}")

    query = "帮我查一下清华大学的李明教授的信息"
    print(f"  查询: {query}")

    start = time.time()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是考研辅导助手。当用户问到导师信息时，使用 search_tutor 工具查询。",
            },
            {"role": "user", "content": query},
        ],
        tools=TOOLS,
        temperature=0.1,
    )
    elapsed = round(time.time() - start, 2)

    msg = response.choices[0].message
    print(f"  耗时: {elapsed}s")
    print(f"  finish_reason: {response.choices[0].finish_reason}")

    if msg.tool_calls:
        for tc in msg.tool_calls:
            print(f"  工具调用: {tc.function.name}")
            print(f"  参数: {tc.function.arguments}")
            args = json.loads(tc.function.arguments)
            # 验证参数合理性
            has_name = "name" in args or "author_name" in args
            print(f"  参数包含姓名: {has_name}")
        print(f"[OK] Function Calling 正常")
        return True
    else:
        print(f"  回答 (未调用工具): {msg.content[:200]}")
        print(f"[FAIL] 模型未返回 tool_calls")
        return False


def test_tool_result_processing(client):
    """测试 3: 工具结果处理 (完整 ReAct 循环)"""
    print(f"\n{'='*50}")
    print(f"测试 3: 工具结果处理 (模拟 ReAct)")
    print(f"{'='*50}")

    messages = [
        {
            "role": "system",
            "content": "你是考研辅导助手。使用工具查询信息后，以"导师信息卡片"格式回答用户。",
        },
        {"role": "user", "content": "清华大学李明教授的研究方向是什么？今年招生吗？"},
    ]

    # 第一轮: 获取 tool_call
    response = client.chat.completions.create(
        model=MODEL, messages=messages, tools=TOOLS, temperature=0.1
    )
    msg = response.choices[0].message

    if not msg.tool_calls:
        print(f"  第一轮未调用工具: {msg.content[:200]}")
        print(f"[FAIL] ReAct 循环失败")
        return False

    tc = msg.tool_calls[0]
    print(f"  第一轮: 调用 {tc.function.name}({tc.function.arguments})")

    # 拼接工具结果
    messages.append(msg)
    mock_result = MOCK_RESULTS.get(tc.function.name, '{"error": "unknown tool"}')
    messages.append(
        {"role": "tool", "tool_call_id": tc.id, "content": mock_result}
    )

    # 第二轮: 期望模型给出最终回答
    response2 = client.chat.completions.create(
        model=MODEL, messages=messages, tools=TOOLS, temperature=0.1
    )
    msg2 = response2.choices[0].message

    if msg2.content:
        print(f"  第二轮: 最终回答:")
        for line in msg2.content.split("\n")[:15]:
            print(f"    {line}")
        print(f"[OK] ReAct 循环完成")
        return True
    elif msg2.tool_calls:
        print(f"  第二轮: 又调用了工具 {msg2.tool_calls[0].function.name}")
        print(f"[WARN] 模型选择继续调用工具（可接受，但需要更多循环）")
        return True
    else:
        print(f"[FAIL] 第二轮无输出")
        return False


def test_multi_tool_selection(client):
    """测试 4: 多工具选择"""
    print(f"\n{'='*50}")
    print(f"测试 4: 多工具选择能力")
    print(f"{'='*50}")

    test_cases = [
        ("李明教授最近发了什么论文？", "get_publications"),
        ("清华大学计算机系今年招多少人？", "query_knowledge_base"),
        ("帮我查一下北大的王老师", "search_tutor"),
    ]

    correct = 0
    for query, expected_tool in test_cases:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是考研辅导助手，根据用户问题选择合适的工具。"},
                {"role": "user", "content": query},
            ],
            tools=TOOLS,
            temperature=0.1,
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            actual_tool = msg.tool_calls[0].function.name
            match = actual_tool == expected_tool
            status = "OK" if match else "MISS"
            print(f"  [{status}] \"{query}\" → {actual_tool} (期望: {expected_tool})")
            if match:
                correct += 1
        else:
            print(f"  [MISS] \"{query}\" → 未调用工具 (期望: {expected_tool})")

    rate = correct / len(test_cases) * 100
    print(f"\n  工具选择准确率: {correct}/{len(test_cases)} = {rate:.0f}%")
    print(f"[{'OK' if rate >= 66 else 'FAIL'}] 多工具选择测试")
    return rate >= 66


def test_stability(client, rounds: int = 5):
    """测试 5: Function Calling 稳定性"""
    print(f"\n{'='*50}")
    print(f"测试 5: Function Calling 稳定性 ({rounds} 轮)")
    print(f"{'='*50}")

    query = "帮我查一下浙江大学的陈教授"
    success = 0

    for i in range(rounds):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "你是考研辅导助手。查询导师信息时必须使用 search_tutor 工具。"},
                    {"role": "user", "content": query},
                ],
                tools=TOOLS,
                temperature=0.1,
            )
            msg = response.choices[0].message
            if msg.tool_calls and msg.tool_calls[0].function.name == "search_tutor":
                args = json.loads(msg.tool_calls[0].function.arguments)
                print(f"  [{i+1}] OK - search_tutor({json.dumps(args, ensure_ascii=False)})")
                success += 1
            elif msg.tool_calls:
                print(f"  [{i+1}] WRONG TOOL - {msg.tool_calls[0].function.name}")
            else:
                print(f"  [{i+1}] NO CALL - {msg.content[:80]}")
        except Exception as e:
            print(f"  [{i+1}] ERROR - {e}")

    rate = success / rounds * 100
    print(f"\n  成功率: {success}/{rounds} = {rate:.0f}%")
    print(f"[{'OK' if rate >= 80 else 'FAIL'}] 稳定性测试 ({'通过' if rate >= 80 else '不达标，考虑换模型'})")
    return rate >= 80


def main():
    print("=" * 60)
    print("  KaoBot POC - LLM Function Calling 验证")
    print(f"  模型: {MODEL} @ {BASE_URL}")
    print("=" * 60)

    client = get_client()

    # 先测试连接
    try:
        client.models.list()
        print(f"[OK] LLM 服务连接成功")
    except Exception as e:
        print(f"[ERROR] 无法连接 LLM 服务: {e}")
        print(f"  请确保 Ollama 已运行: ollama serve")
        print(f"  并已拉取模型: ollama pull {MODEL}")
        sys.exit(1)

    results = {}
    results["基础对话"] = test_basic_chat(client)
    results["单工具调用"] = test_single_tool_call(client)
    results["工具结果处理"] = test_tool_result_processing(client)
    results["多工具选择"] = test_multi_tool_selection(client)
    results["稳定性"] = test_stability(client)

    # 总结
    print(f"\n{'='*60}")
    print(f"  Function Calling 验证总结")
    print(f"{'='*60}")
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        if not passed:
            all_pass = False

    if all_pass:
        print(f"\n  结论: {MODEL} Function Calling 能力满足需求")
    else:
        print(f"\n  结论: {MODEL} 部分测试未通过")
        print(f"  建议: 尝试更大模型 (qwen3:32b) 或 DeepSeek API")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
