"""LLM 过滤 CLI — 对采集结果中的评论进行意向分类。

用法:
    DEEPSEEK_API_KEY="sk-xxx" python tools/llm_filter.py results.json
    python tools/llm_filter.py results.json --goal "筛选有购买意向的用户"
"""

import json
import os
import sys
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _env import load_env  # noqa: E402
from _llm import classify, call_deepseek, parse_llm_json  # noqa: E402
from _normalize import normalize_batch  # noqa: E402


# ---- 参数解析 ----

def _parse_args(argv: list[str]) -> dict:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    goal = ""
    input_path = None
    for i, arg in enumerate(argv):
        if arg == "--api-key" and i + 1 < len(argv):
            api_key = argv[i + 1]
        elif arg == "--goal" and i + 1 < len(argv):
            goal = argv[i + 1]
        elif not arg.startswith("--") and i > 0:
            input_path = arg
    return {"api_key": api_key, "goal": goal, "input_path": input_path}


def _make_output_path(input_path: Path) -> Path:
    stem = input_path.stem
    if "extracted" in stem:
        stem = stem.replace("extracted", "filtered")
    else:
        stem += "_filtered"
    out = input_path.parent / f"{stem}.json"
    if out.resolve() == input_path.resolve():
        out = input_path.parent / f"{input_path.stem}_filtered_out.json"
    return out


# ---- 意向分析 ----

def extract_intents(items: list[dict], goal: str, api_key: str) -> list[dict]:
    """拼接评论与帖子上下文，批量提取意向。"""
    enriched: list[dict] = []
    for item in items:
        parent_author = item.get("author", "")
        parent_content = item.get("content", {}).get("title", "")
        for c in item.get("comments") or []:
            enriched.append({
                "author": c.get("author", ""),
                "content": c.get("content", ""),
                "parent_author": parent_author,
                "parent_content": parent_content,
            })

    if not enriched:
        print("没有评论可供分析。")
        return items

    print(f"\n--- 意向分析（目标: {goal}）---")
    print(f"  共 {len(enriched)} 条评论, 分析中...\n")

    results: list[dict] = []
    try:
        results = classify(enriched, goal, api_key=api_key)
    except Exception as e:
        print(f"  ⚠️  意向分析异常: {e}")
        return items

    # 写回评论的 intent 字段
    idx = 0
    counts = {"HIGH": 0, "INTERESTED": 0, "NONE": 0}
    for item in items:
        for c in item.get("comments") or []:
            if idx < len(results):
                c["intent"] = results[idx].get("intent", "NONE")
                counts[c["intent"]] = counts.get(c["intent"], 0) + 1
                idx += 1

    # 打印摘要
    for i, r in enumerate(results):
        it = r.get("intent", "?")
        icon = {"HIGH": "🔥", "INTERESTED": "👀", "NONE": "—"}.get(it, "?")
        author = r.get("author", "?")
        content = r.get("content", "")[:60]
        print(f"  [{i+1}] {icon} {it:<12} @{author}: {content}")

    print(f"\n  结果: 🔥{counts['HIGH']} 强烈 / 👀{counts['INTERESTED']} 观望 / —{counts['NONE']} 无关")
    return items


# ---- 发现提取 ----

def extract_findings(high_items: list[dict], goal: str, api_key: str) -> list[str]:
    """汇总 HIGH 意向评论，提取核心发现。"""
    if not high_items:
        return ["未发现高意向用户。"]

    print(f"\n--- 提取核心发现（{len(high_items)} 条 HIGH 意向）---")

    summary = ""
    for i, t in enumerate(high_items):
        author = t.get("author", "?")
        content = t.get("content", "")[:250]
        parent = t.get("parent_content", "")[:100]
        summary += f"[{i+1}] @{author}: {content}\n  (原帖: {parent})\n"

    prompt = f"""基于以下高购买意向的评论，提取 3 条核心发现。每条用一句话概括。

用户目标：{goal}

高意向评论：
{summary[:4000]}

请以 JSON 数组格式回复：
["发现1", "发现2", "发现3"]
如果内容不足以提取 3 条，有几条写几条。"""

    try:
        raw = call_deepseek(
            [
                {"role": "system", "content": "你是一个信息分析师。只回复 JSON 数组。"},
                {"role": "user", "content": prompt},
            ],
            api_key=api_key,
            temperature=0.3,
            max_tokens=800,
        )
        findings = parse_llm_json(raw)
        if isinstance(findings, list):
            for i, f in enumerate(findings):
                print(f"  [{i+1}] {f}")
            return findings
    except Exception as e:
        print(f"  API 异常: {e}")
    return []


# ---- 主流程 ----

def main():
    load_env()
    args = _parse_args(sys.argv)
    api_key = args["api_key"]
    goal = args["goal"]
    input_path_str = args["input_path"]

    if not input_path_str:
        print('用法: python tools/llm_filter.py <results.json> [--goal "..."] [--api-key sk-xxx]')
        print("  或设置环境变量 DEEPSEEK_API_KEY")
        sys.exit(1)
    if not api_key:
        print("错误: DEEPSEEK_API_KEY 未设置。")
        sys.exit(1)

    input_path = Path(input_path_str)
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if "items" in raw:
        items = raw["items"]
    else:
        tweets = raw.get("tweets", raw.get("videos", []))
        platform = "twitter"
        if raw.get("search_query") or any(
            t.get("view_count") or t.get("duration") for t in tweets
        ):
            platform = "youtube"
        items = normalize_batch(tweets, platform)
    search_term = raw.get("search_term", raw.get("search_query", ""))

    if not items:
        print("没有需要处理的数据。")
        sys.exit(0)

    t0 = datetime.now(dt_timezone.utc)

    items = extract_intents(items, goal, api_key)

    # 收集 HIGH 意向评论
    high_comments = []
    for item in items:
        for c in item.get("comments") or []:
            if c.get("intent") == "HIGH":
                high_comments.append({
                    "author": c.get("author", ""),
                    "content": c.get("content", ""),
                    "parent_content": item.get("content", {}).get("title", ""),
                    "parent_author": item.get("author", ""),
                })

    findings = extract_findings(high_comments, goal, api_key)

    output = {
        "filtered_at": datetime.now(dt_timezone.utc).isoformat(),
        "search_term": search_term,
        "goal": goal or "未指定",
        "high_count": len(high_comments),
        "findings": findings,
        "high_comments": high_comments,
        "items": items,
    }

    out_path = _make_output_path(input_path)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    elapsed = (datetime.now(dt_timezone.utc) - t0).total_seconds()
    print(f"\n{'=' * 60}")
    print(f"  🔥 高意向: {len(high_comments)} 条评论")
    print(f"  发现: {len(findings)} 条")
    print(f"  输出: {out_path}")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
