"""LLM 过滤共享模块 — console.py 和 llm_filter.py 共用。

提供:
  - classify: 构建 prompt → 调 DeepSeek → 返回意向对象列表

用法:
    from _llm import classify

    comments = [{"author": "u1", "content": "怎么买",
                 "parent_author": "poster", "parent_content": "新品发布..."}]
    results = classify(comments, "筛选目标", api_key=key)
    # → [{"author":"u1","intent":"HIGH","content":"怎么买"}, ...]

环境变量: DEEPSEEK_API_KEY
"""

from __future__ import annotations

import json
import os
import time

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

INTENT_HIGH = "HIGH"
INTENT_INTERESTED = "INTERESTED"
INTENT_NONE = "NONE"


class ClassificationError(Exception):
    """LLM 返回格式不正确。"""


# ------------------------------------------------------------------
# 意向分类
# ------------------------------------------------------------------


def classify(
    items: list[dict],
    goal: str,
    *,
    api_key: str | None = None,
) -> list[dict]:
    """对评论进行意向分类，返回作者+意向+内容的对象列表。

    items 格式: {author, content, parent_author, parent_content}
    返回: [{author, intent: HIGH|INTERESTED|NONE, content}, ...]
    """
    if not items:
        return []

    blocks = []
    for i, it in enumerate(items):
        blocks.append(
            f"[{i+1}]\n"
            f"  原帖 @{it.get('parent_author', '?')}: {it.get('parent_content', '')[:200]}\n"
            f"  评论 @{it.get('author', '?')}: {it.get('content', '')[:300]}"
        )

    prompt = (
        f"判断以下每条评论中，用户是否对筛选目标有购买/使用意向。"
        f"返回 JSON 对象数组。\n\n"
        f"筛选目标：{goal}\n\n"
        f"意向定义：\n"
        f"  HIGH — 明确表达了购买、使用、尝试或获取的意愿"
        f"（如\"怎么买\"\"求链接\"\"在哪下载\"\"想试试\"\"推荐一下\"）\n"
        f"  INTERESTED — 表现出兴趣但处于观望"
        f"（如\"看起来不错\"\"有点意思\"\"收藏了\"\"Mark\"\"关注一下\"）\n"
        f"  NONE — 没有表现出任何兴趣，或与目标无关\n\n"
        + "\n---\n".join(blocks)
        + f"\n---\n\n"
        f"逐条判断, 仅回复 JSON 对象数组:\n"
        f'[{{"author":"用户名","intent":"HIGH|INTERESTED|NONE","content":"评论内容"}},...]\n'
        f"数组长度 = {len(items)}。"
    )

    raw = call_deepseek(
        [
            {"role": "system", "content": "你是一个意向分析助手。只回复 JSON 对象数组。每个对象含 author/intent/content 三个字段。"},
            {"role": "user", "content": prompt},
        ],
        api_key=api_key,
        max_tokens=len(items) * 60 + 50,
    )
    arr = parse_llm_json(raw)
    if isinstance(arr, list) and len(arr) == len(items):
        return [_normalize_intent(obj) for obj in arr]
    raise ClassificationError(
        f"LLM 返回格式错误: 期望 {len(items)} 个对象, "
        f"实际 {type(arr).__name__}"
    )


def _normalize_intent(obj: dict) -> dict:
    """归一化单个意向对象。"""
    raw = str(obj.get("intent", "")).upper()
    if "HIGH" in raw or "购买" in raw or "强烈" in raw:
        intent = INTENT_HIGH
    elif "INTEREST" in raw or "感兴趣" in raw or "观望" in raw or "兴趣" in raw:
        intent = INTENT_INTERESTED
    else:
        intent = INTENT_NONE
    return {
        "author": obj.get("author") or None,
        "intent": intent,
        "content": obj.get("content") or None,
    }


# ------------------------------------------------------------------
# JSON 解析 (容忍 markdown 代码块)
# ------------------------------------------------------------------


def parse_llm_json(raw: str) -> list | dict | None:
    """解析 LLM 返回的 JSON,容忍 ```json ... ``` 包裹。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------------
# DeepSeek API 调用
# ------------------------------------------------------------------


def call_deepseek(
    messages: list[dict],
    api_key: str | None = None,
    *,
    max_tokens: int = 100,
    temperature: float = 0.1,
    timeout: int = 30,
    retries: int = 1,
) -> str:
    """调用 DeepSeek chat API,返回 content 文本。

    api_key 优先从参数,其次从环境变量 DEEPSEEK_API_KEY。
    失败自动重试;重试后仍失败则抛出异常。
    """
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY 未设置")

    import requests

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                DEEPSEEK_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "model": MODEL,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(1)
    raise last_exc  # type: ignore[misc]
