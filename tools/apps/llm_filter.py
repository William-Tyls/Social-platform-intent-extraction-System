"""LLM 过滤 CLI — 读取采集结果，调用 DeepSeek 分类并提取发现。

用法:
    DEEPSEEK_API_KEY="sk-xxx" python tools/llm_filter.py twitter_results.json
    python tools/llm_filter.py twitter_results.json --goal "筛选真实租户"

依赖: pip install openai
"""

import json
import os
import sys
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _env import load_env  # noqa: E402
from _llm import (          # noqa: E402
    build_classify_prompt, build_batch_classify_prompt,
    build_unified_batch_prompt,
    normalize_label, parse_llm_json,
)
from _normalize import normalize_batch  # noqa: E402

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"


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


# ---- 分类 ----

def classify_tweets(tweets: list[dict], goal: str, client, model: str = MODEL) -> list[dict]:
    """批量分类推文，优先一次批量调用，失败回退逐条。"""
    if not goal:
        goal = "通用信息筛选，保留有价值的原创内容，过滤广告和无关帖子"

    print(f"\n--- 分类过滤（目标: {goal}）---")
    print(f"  共 {len(tweets)} 条, 批量分类...\n")

    labels: list[str] = [""] * len(tweets)

    # 第一步: 批量 (优先使用统一 batch prompt)
    try:
        # 检查是否已是归一化格式
        first = tweets[0] if tweets else {}
        if isinstance(first.get("content"), dict):
            batch_prompt = build_unified_batch_prompt(tweets, goal)
        else:
            batch_prompt = build_batch_classify_prompt(tweets, goal)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个信息过滤助手。只回复 JSON 数组。"},
                {"role": "user", "content": batch_prompt},
            ],
            temperature=0.1,
            max_tokens=len(tweets) * 15 + 20,
        )
        parsed = parse_llm_json(resp.choices[0].message.content)
        if isinstance(parsed, list) and len(parsed) == len(tweets):
            labels = [normalize_label(str(x)) for x in parsed]
        else:
            print(f"  ⚠️ 批量返回长度不匹配, 回退逐条")
    except Exception as e:
        print(f"  ⚠️ 批量分类异常: {e}, 回退逐条")

    # 第二步: 逐条兜底
    for i, t in enumerate(tweets):
        if labels[i]:
            continue
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个信息过滤助手。严格按格式回复。"},
                    {"role": "user", "content": build_classify_prompt(t, goal)},
                ],
                temperature=0.1,
                max_tokens=10,
            )
            labels[i] = normalize_label(resp.choices[0].message.content)
        except Exception as e:
            print(f"  [{i+1}] API 异常: {e}")
            labels[i] = "ERROR"

    # 组装
    labeled = []
    counts = {"TARGET": 0, "AD": 0, "IRRELEVANT": 0, "ERROR": 0}
    for i, t in enumerate(tweets):
        label = labels[i]
        counts[label] = counts.get(label, 0) + 1
        t_label = dict(t)
        t_label["label"] = label
        labeled.append(t_label)
        icon = {"TARGET": "✅", "AD": "📢", "IRRELEVANT": "❌", "ERROR": "⚠️"}.get(label, "?")
        author = t.get("author", t.get("author_handle", "?"))
        print(f"  [{i+1}] {icon} {label:<12} @{author}")

    print(f"\n  结果: {counts['TARGET']} 目标 / {counts['AD']} 广告 / {counts['IRRELEVANT']} 无关")
    return labeled


# ---- 发现提取 ----

def extract_findings(target_tweets: list[dict], goal: str, client, model: str = MODEL) -> list[str]:
    """汇总目标推文，提取核心发现。"""
    if not target_tweets:
        return ["无符合条件的帖文。"]

    print(f"\n--- 提取核心发现（{len(target_tweets)} 条）---")

    summary = ""
    for i, t in enumerate(target_tweets):
        author = t.get("author", t.get("author_handle", "?"))
        text = t.get("content", {}).get("title", "") or t.get("tweet_text", "")
        summary += f"帖文{i+1} (@{author}): {text[:300]}\n"
        if t.get("comments"):
            for c in t["comments"][:3]:
                handle = c.get("author", c.get("commenter_handle", "?"))
                body = c.get("content", c.get("text", ""))
                summary += f"  评论: @{handle}: {body[:150]}\n"

    prompt = f"""基于以下搜索结果，提取 3 条核心发现。每条用一句话概括。

用户目标：{goal}

搜索结果：
{summary[:4000]}

请以 JSON 数组格式回复：
["发现1", "发现2", "发现3"]
如果内容不足以提取 3 条，有几条写几条。"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个信息分析师。只回复 JSON 数组。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        findings = parse_llm_json(resp.choices[0].message.content)
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
        print('用法: python tools/llm_filter.py <extracted.json> [--goal "..."] [--api-key sk-xxx]')
        print("  或设置环境变量 DEEPSEEK_API_KEY")
        sys.exit(1)
    if not api_key:
        print("错误: DEEPSEEK_API_KEY 未设置。")
        sys.exit(1)

    input_path = Path(input_path_str)
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    # 兼容新旧两种导出格式
    if "items" in raw:
        tweets = raw["items"]  # 归一化格式
    else:
        tweets = raw.get("tweets", raw.get("videos", []))
        # 旧格式自动归一化
        platform = "twitter"
        if raw.get("search_query") or any(t.get("view_count") or t.get("duration") for t in tweets):
            platform = "youtube"
        tweets = normalize_batch(tweets, platform)
    search_term = raw.get("search_term", raw.get("search_query", ""))

    if not tweets:
        print("没有需要处理的数据。")
        sys.exit(0)

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    t0 = datetime.now(dt_timezone.utc)

    labeled = classify_tweets(tweets, goal, client)
    target_tweets = [t for t in labeled if t["label"] == "TARGET"]
    findings = extract_findings(target_tweets, goal, client)

    output = {
        "filtered_at": datetime.now(dt_timezone.utc).isoformat(),
        "search_term": search_term,
        "goal": goal or "未指定",
        "total": len(tweets),
        "kept": len(target_tweets),
        "discarded": len(tweets) - len(target_tweets),
        "findings": findings,
        "items": labeled,
    }

    out_path = _make_output_path(input_path)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    elapsed = (datetime.now(dt_timezone.utc) - t0).total_seconds()
    print(f"\n{'=' * 60}")
    print(f"  保留: {len(target_tweets)} / 丢弃: {len(tweets) - len(target_tweets)}")
    print(f"  发现: {len(findings)} 条")
    print(f"  输出: {out_path}")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
