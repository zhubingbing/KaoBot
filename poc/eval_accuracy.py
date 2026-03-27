"""
PageIndex 准确率评测脚本
========================
针对已索引的导师信息文档，运行标准化评测题目，统计准确率。

使用方法:
  1. 先用 test_pageindex_basic.py 上传文档，获取 doc_id
  2. 编辑 eval_questions.json 填写评测题目和标准答案
  3. 运行: python poc/eval_accuracy.py --doc-id <doc_id>

评测维度:
  - 姓名提取准确率
  - 研究方向匹配率
  - 招生状态判断准确率
  - 联系方式提取准确率
  - 综合问答准确率
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 默认评测题目模板 (用户需要根据实际文档修改)
DEFAULT_QUESTIONS = [
    # ─── 姓名提取 ───
    {
        "id": "Q01",
        "category": "姓名提取",
        "question": "文档中提到了哪些导师？请列出他们的姓名。",
        "expected_keywords": [],  # 填入实际导师姓名
        "match_type": "contains_all",
    },
    # ─── 研究方向 ───
    {
        "id": "Q02",
        "category": "研究方向",
        "question": "XXX教授的主要研究方向是什么？",
        "expected_keywords": [],  # 填入实际研究方向关键词
        "match_type": "contains_any",
    },
    {
        "id": "Q03",
        "category": "研究方向",
        "question": "哪些导师的研究方向涉及人工智能？",
        "expected_keywords": [],
        "match_type": "contains_any",
    },
    # ─── 招生状态 ───
    {
        "id": "Q04",
        "category": "招生状态",
        "question": "XXX教授今年是否招收研究生？",
        "expected_keywords": [],  # 如 ["招收", "2名"] 或 ["不招"]
        "match_type": "contains_any",
    },
    {
        "id": "Q05",
        "category": "招生状态",
        "question": "哪些导师今年有招生名额？",
        "expected_keywords": [],
        "match_type": "contains_any",
    },
    # ─── 联系方式 ───
    {
        "id": "Q06",
        "category": "联系方式",
        "question": "XXX教授的联系邮箱是什么？",
        "expected_keywords": [],  # 如 ["xxx@xxx.edu.cn"]
        "match_type": "contains_any",
    },
    # ─── 职称信息 ───
    {
        "id": "Q07",
        "category": "职称",
        "question": "XXX是教授还是副教授？",
        "expected_keywords": [],
        "match_type": "contains_any",
    },
    # ─── 综合查询 ───
    {
        "id": "Q08",
        "category": "综合",
        "question": "请给我XXX教授的完整信息，包括研究方向、招生状态和联系方式。",
        "expected_keywords": [],
        "match_type": "contains_all",
    },
    {
        "id": "Q09",
        "category": "综合",
        "question": "对比XXX和YYY两位教授的研究方向有什么不同？",
        "expected_keywords": [],
        "match_type": "contains_any",
    },
    {
        "id": "Q10",
        "category": "综合",
        "question": "如果我对自然语言处理感兴趣，你推荐我联系哪位导师？为什么？",
        "expected_keywords": [],
        "match_type": "contains_any",
    },
]


def load_questions(path: str = None) -> list[dict]:
    """加载评测题目"""
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # 生成模板文件
    template_path = "poc/eval_questions.json"
    if not os.path.exists(template_path):
        with open(template_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_QUESTIONS, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 已生成评测题目模板: {template_path}")
        print(f"  请编辑此文件，填入实际的导师姓名、研究方向等关键词")
        print(f"  然后重新运行此脚本")
        sys.exit(0)

    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_answer(answer: str, question: dict) -> dict:
    """评估单个回答"""
    keywords = question.get("expected_keywords", [])
    match_type = question.get("match_type", "contains_any")

    if not keywords:
        return {"correct": None, "reason": "未设置标准答案关键词", "matched": [], "missed": []}

    matched = [kw for kw in keywords if kw.lower() in answer.lower()]
    missed = [kw for kw in keywords if kw.lower() not in answer.lower()]

    if match_type == "contains_all":
        correct = len(missed) == 0
    elif match_type == "contains_any":
        correct = len(matched) > 0
    else:
        correct = len(matched) > 0

    return {
        "correct": correct,
        "reason": f"匹配 {len(matched)}/{len(keywords)} 个关键词",
        "matched": matched,
        "missed": missed,
    }


def run_evaluation(doc_id: str, questions: list[dict]):
    """运行评测"""
    from pageindex import PageIndexClient

    api_key = os.getenv("PAGEINDEX_API_KEY")
    client = PageIndexClient(api_key=api_key)

    results = []
    for q in questions:
        if not q.get("expected_keywords"):
            print(f"  [{q['id']}] 跳过 (未设置标准答案)")
            continue

        print(f"  [{q['id']}] {q['category']}: {q['question'][:50]}...", end=" ", flush=True)

        start = time.time()
        try:
            response = client.chat_completions(
                messages=[{"role": "user", "content": q["question"]}],
                doc_id=doc_id,
            )
            answer = response["choices"][0]["message"]["content"]
            elapsed = round(time.time() - start, 2)

            eval_result = evaluate_answer(answer, q)
            eval_result.update({
                "id": q["id"],
                "category": q["category"],
                "question": q["question"],
                "answer": answer,
                "elapsed": elapsed,
            })

            status = "PASS" if eval_result["correct"] else "FAIL"
            print(f"[{status}] ({elapsed}s)")
            results.append(eval_result)

        except Exception as e:
            print(f"[ERROR] {e}")
            results.append({
                "id": q["id"],
                "category": q["category"],
                "question": q["question"],
                "correct": False,
                "reason": f"API 错误: {e}",
                "answer": "",
                "elapsed": 0,
            })

    return results


def generate_report(results: list[dict], doc_id: str) -> str:
    """生成评测报告"""
    total = len(results)
    correct = sum(1 for r in results if r.get("correct") is True)
    incorrect = sum(1 for r in results if r.get("correct") is False)
    skipped = sum(1 for r in results if r.get("correct") is None)
    accuracy = correct / (correct + incorrect) * 100 if (correct + incorrect) > 0 else 0
    avg_time = sum(r.get("elapsed", 0) for r in results) / total if total > 0 else 0

    # 按类别统计
    categories = {}
    for r in results:
        cat = r.get("category", "未分类")
        if cat not in categories:
            categories[cat] = {"total": 0, "correct": 0}
        if r.get("correct") is not None:
            categories[cat]["total"] += 1
            if r["correct"]:
                categories[cat]["correct"] += 1

    report = f"""# PageIndex 准确率评测报告

> 日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}
> doc_id: {doc_id}

## 总体结果

| 指标 | 值 |
|------|-----|
| 总题数 | {total} |
| 正确 | {correct} |
| 错误 | {incorrect} |
| 跳过 | {skipped} |
| **准确率** | **{accuracy:.1f}%** |
| 平均响应时间 | {avg_time:.1f}s |

## 按类别统计

| 类别 | 正确/总数 | 准确率 |
|------|----------|--------|
"""
    for cat, stats in categories.items():
        cat_acc = stats["correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
        report += f"| {cat} | {stats['correct']}/{stats['total']} | {cat_acc:.0f}% |\n"

    report += "\n## 详细结果\n\n"
    for r in results:
        status = "PASS" if r.get("correct") else ("FAIL" if r.get("correct") is False else "SKIP")
        report += f"### [{status}] {r['id']}: {r.get('question', 'N/A')}\n\n"
        report += f"- 类别: {r.get('category')}\n"
        report += f"- 耗时: {r.get('elapsed', 0)}s\n"
        report += f"- 评判: {r.get('reason')}\n"
        if r.get("missed"):
            report += f"- 缺失关键词: {r['missed']}\n"
        if r.get("answer"):
            answer_preview = r["answer"][:500].replace("\n", "\n  > ")
            report += f"- 回答:\n  > {answer_preview}\n"
        report += "\n"

    report += f"""## 结论

"""
    if accuracy >= 95:
        report += "**PageIndex 准确率达标 (>= 95%)，可以作为 KaoBot 知识库核心方案。**\n"
    elif accuracy >= 85:
        report += "PageIndex 准确率接近目标 (85-95%)，建议优化 prompt 或文档格式后重新评测。\n"
    else:
        report += "PageIndex 准确率不达标 (< 85%)，需要分析失败原因或考虑替代方案。\n"

    return report


def main():
    parser = argparse.ArgumentParser(description="PageIndex 准确率评测")
    parser.add_argument("--doc-id", required=True, help="已索引文档的 doc_id")
    parser.add_argument("--questions", help="评测题目 JSON 文件路径")
    args = parser.parse_args()

    print("=" * 60)
    print("  KaoBot POC - PageIndex 准确率评测")
    print(f"  doc_id: {args.doc_id}")
    print("=" * 60)

    questions = load_questions(args.questions)
    valid_questions = [q for q in questions if q.get("expected_keywords")]
    print(f"\n  共 {len(questions)} 道题目, {len(valid_questions)} 道有标准答案\n")

    if not valid_questions:
        print("[WARN] 没有设置标准答案的题目")
        print("  请编辑 poc/eval_questions.json 填入 expected_keywords")
        sys.exit(0)

    results = run_evaluation(args.doc_id, questions)

    # 生成报告
    report = generate_report(results, args.doc_id)
    report_path = "poc/eval_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[OK] 评测报告已生成: {report_path}")

    # 打印摘要
    correct = sum(1 for r in results if r.get("correct") is True)
    total_valid = sum(1 for r in results if r.get("correct") is not None)
    accuracy = correct / total_valid * 100 if total_valid > 0 else 0
    print(f"\n  准确率: {correct}/{total_valid} = {accuracy:.1f}%")
    target = "PASS" if accuracy >= 95 else "FAIL"
    print(f"  目标 (>= 95%): [{target}]")


if __name__ == "__main__":
    main()
