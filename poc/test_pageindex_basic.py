"""
PageIndex POC - 基础功能验证
=============================
验证 PageIndex 作为 KaoBot 知识库核心的可行性。

使用方法:
  1. 复制 .env.example 为 .env 并填入 PAGEINDEX_API_KEY
  2. 准备一份导师信息 PDF 文件
  3. 运行: python poc/test_pageindex_basic.py --pdf <文件路径>

验证项:
  - 文档上传 (submit_document)
  - 索引状态轮询 (get_document)
  - 基础查询 (chat_completions)
  - 来源引用 (retrieval API)
  - 树结构查看 (get_tree)
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()


def get_client():
    """初始化 PageIndex 客户端"""
    from pageindex import PageIndexClient

    api_key = os.getenv("PAGEINDEX_API_KEY")
    if not api_key or api_key == "your_pageindex_api_key_here":
        print("[ERROR] 请在 .env 中设置 PAGEINDEX_API_KEY")
        print("  获取地址: https://dash.pageindex.ai")
        sys.exit(1)

    client = PageIndexClient(api_key=api_key)
    print("[OK] PageIndex 客户端初始化成功")
    return client


def test_upload(client, pdf_path: str) -> str:
    """测试 1: 文档上传"""
    print(f"\n{'='*50}")
    print(f"测试 1: 文档上传")
    print(f"文件: {pdf_path}")
    print(f"{'='*50}")

    if not os.path.exists(pdf_path):
        print(f"[ERROR] 文件不存在: {pdf_path}")
        sys.exit(1)

    result = client.submit_document(pdf_path)
    doc_id = result["doc_id"]
    print(f"[OK] 上传成功, doc_id: {doc_id}")
    print(f"  完整响应: {json.dumps(result, ensure_ascii=False, indent=2)}")
    return doc_id


def test_poll_status(client, doc_id: str, timeout: int = 300):
    """测试 2: 索引状态轮询"""
    print(f"\n{'='*50}")
    print(f"测试 2: 索引状态轮询")
    print(f"doc_id: {doc_id}")
    print(f"{'='*50}")

    start = time.time()
    while time.time() - start < timeout:
        doc_info = client.get_document(doc_id)
        status = doc_info["status"]
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] 状态: {status}")

        if status == "completed":
            print(f"[OK] 索引完成, 耗时 {elapsed} 秒")
            return True
        elif status in ("failed", "error"):
            print(f"[FAIL] 索引失败: {json.dumps(doc_info, ensure_ascii=False)}")
            return False

        time.sleep(10)

    print(f"[FAIL] 超时 ({timeout}s)")
    return False


def test_basic_query(client, doc_id: str, query: str = None):
    """测试 3: 基础查询"""
    print(f"\n{'='*50}")
    print(f"测试 3: 基础查询 (chat_completions)")
    print(f"{'='*50}")

    if query is None:
        query = "请列出这份文档中提到的所有导师姓名和他们的研究方向"

    print(f"  查询: {query}")

    start = time.time()
    response = client.chat_completions(
        messages=[{"role": "user", "content": query}],
        doc_id=doc_id,
    )
    elapsed = round(time.time() - start, 2)

    answer = response["choices"][0]["message"]["content"]
    usage = response.get("usage", {})

    print(f"  耗时: {elapsed}s")
    print(f"  Token 用量: {json.dumps(usage)}")
    print(f"\n  回答:\n  {'─'*40}")
    for line in answer.split("\n"):
        print(f"  {line}")
    print(f"  {'─'*40}")
    print(f"[OK] 查询成功")
    return answer


def test_query_with_citation(client, doc_id: str):
    """测试 4: 带来源引用的查询"""
    print(f"\n{'='*50}")
    print(f"测试 4: 带来源引用的查询")
    print(f"{'='*50}")

    query = "请告诉我文档中导师的研究方向，并在回答中标注信息来自第几页"
    print(f"  查询: {query}")

    start = time.time()
    response = client.chat_completions(
        messages=[{"role": "user", "content": query}],
        doc_id=doc_id,
    )
    elapsed = round(time.time() - start, 2)

    answer = response["choices"][0]["message"]["content"]
    print(f"  耗时: {elapsed}s")
    print(f"\n  回答:\n  {'─'*40}")
    for line in answer.split("\n"):
        print(f"  {line}")
    print(f"  {'─'*40}")

    has_page_ref = any(kw in answer for kw in ["页", "page", "Page", "第", "p.", "P."])
    print(f"  包含页码引用: {'YES' if has_page_ref else 'NO'}")
    print(f"[{'OK' if has_page_ref else 'WARN'}] 来源引用测试{'通过' if has_page_ref else '未检测到页码引用'}")
    return answer


def test_retrieval_api(client, doc_id: str):
    """测试 5: Retrieval API (获取精确页码引用)"""
    print(f"\n{'='*50}")
    print(f"测试 5: Retrieval API (精确来源引用)")
    print(f"{'='*50}")

    query = "这份文档中有哪些导师在招收研究生？"
    print(f"  查询: {query}")

    try:
        retrieval = client.submit_retrieval_query(
            doc_id=doc_id,
            query=query,
        )
        retrieval_id = retrieval["retrieval_id"]
        print(f"  retrieval_id: {retrieval_id}")

        # 轮询结果
        for _ in range(30):
            result = client.get_retrieval_result(retrieval_id)
            if result["status"] == "completed":
                break
            time.sleep(5)

        if result["status"] == "completed":
            nodes = result.get("retrieved_nodes", [])
            print(f"  检索到 {len(nodes)} 个相关节点:")
            for node in nodes:
                title = node.get("title", "N/A")
                node_id = node.get("node_id", "N/A")
                print(f"\n  节点: {title} (id: {node_id})")
                for content in node.get("relevant_contents", []):
                    page = content.get("page_index", "?")
                    text = content.get("relevant_content", "")[:200]
                    print(f"    第 {page} 页: {text}...")
            print(f"[OK] Retrieval API 测试成功")
        else:
            print(f"[FAIL] Retrieval 超时, 状态: {result['status']}")
    except Exception as e:
        print(f"[WARN] Retrieval API 调用失败: {e}")
        print("  (Retrieval API 可能是付费功能或已弃用，Chat API 可作为替代)")


def test_tree_structure(client, doc_id: str):
    """测试 6: 查看文档树结构"""
    print(f"\n{'='*50}")
    print(f"测试 6: 文档树结构 (get_tree)")
    print(f"{'='*50}")

    try:
        tree_result = client.get_tree(doc_id)
        if tree_result.get("status") == "completed":
            tree = tree_result.get("result", {})
            print(f"  树结构 (前 2000 字符):")
            tree_str = json.dumps(tree, ensure_ascii=False, indent=2)
            print(f"  {tree_str[:2000]}")
            if len(tree_str) > 2000:
                print(f"  ... (共 {len(tree_str)} 字符)")
            print(f"[OK] 树结构获取成功")
        else:
            print(f"[WARN] 树结构未就绪: {tree_result.get('status')}")
    except Exception as e:
        print(f"[WARN] get_tree 调用失败: {e}")


def test_streaming(client, doc_id: str):
    """测试 7: 流式输出"""
    print(f"\n{'='*50}")
    print(f"测试 7: 流式输出 (streaming)")
    print(f"{'='*50}")

    query = "简要总结这份文档的主要内容"
    print(f"  查询: {query}")
    print(f"  流式回答: ", end="", flush=True)

    try:
        stream = client.chat_completions(
            messages=[{"role": "user", "content": query}],
            doc_id=doc_id,
            stream=True,
        )
        full_text = ""
        for chunk in stream:
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                print(content, end="", flush=True)
                full_text += content
        print()
        print(f"[OK] 流式输出成功, 共 {len(full_text)} 字符")
    except Exception as e:
        print(f"\n[WARN] 流式输出失败: {e}")
        print("  (可能不支持 stream 参数，Chat API 非流式模式可正常使用)")


def main():
    parser = argparse.ArgumentParser(description="PageIndex POC 验证脚本")
    parser.add_argument("--pdf", required=True, help="要上传的 PDF 文件路径")
    parser.add_argument("--doc-id", help="已有的 doc_id（跳过上传步骤）")
    parser.add_argument("--query", help="自定义查询问题")
    parser.add_argument("--skip-retrieval", action="store_true", help="跳过 Retrieval API 测试")
    args = parser.parse_args()

    print("=" * 60)
    print("  KaoBot POC - PageIndex 基础功能验证")
    print("=" * 60)

    client = get_client()

    # 测试 1: 上传 或 使用已有 doc_id
    if args.doc_id:
        doc_id = args.doc_id
        print(f"\n使用已有 doc_id: {doc_id}")
    else:
        doc_id = test_upload(client, args.pdf)
        # 测试 2: 等待索引完成
        if not test_poll_status(client, doc_id):
            print("\n[ABORT] 索引失败，终止测试")
            sys.exit(1)

    # 测试 3: 基础查询
    test_basic_query(client, doc_id, args.query)

    # 测试 4: 带引用查询
    test_query_with_citation(client, doc_id)

    # 测试 5: Retrieval API
    if not args.skip_retrieval:
        test_retrieval_api(client, doc_id)

    # 测试 6: 树结构
    test_tree_structure(client, doc_id)

    # 测试 7: 流式输出
    test_streaming(client, doc_id)

    # 总结
    print(f"\n{'='*60}")
    print(f"  POC 验证完成")
    print(f"  doc_id: {doc_id} (保存此 ID 用于后续测试)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
