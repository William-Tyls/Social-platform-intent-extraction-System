"""LLM 过滤共享模块 — console.py 和 llm_filter.py 共用。

提供:
  - classify: 构建 prompt → 调 API → 解析 → 返回标签 (一站式)
  - normalize_label / parse_comment_labels: 解析辅助

用法:
    from _llm import classify

    # 批量分类帖子/视频
    labels = classify(items, "筛选目标", api_key=key)

    # 逐条兜底
    labels = classify([item], "筛选目标", api_key=key)

    # 分类评论
    labels = classify(comments, "筛选目标", parent=item, api_key=key)

环境变量: DEEPSEEK_API_KEY
"""

from __future__ import annotations

import json
import os
import time

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


# ------------------------------------------------------------------
# 一站式分类
# ------------------------------------------------------------------


class ClassificationError(Exception):
    """LLM 返回格式不正确（非数组、长度不匹配等）。"""


def classify(
    items: list[dict],
    goal: str,
    *,
    parent: dict | None = None,
    api_key: str | None = None,
) -> list[str]:
    """构建 prompt → 调 DeepSeek API → 返回标签列表。

    ``parent`` 为 None 时分类帖子/视频，指定时分类评论（附带原帖上下文）。

    item 格式 (归一化 schema):
        {author, content: {title, body}, meta: {likes, replies, views},
         comments: [{author, content}, ...]}

    返回: ["TARGET", "AD", "IRRELEVANT", ...]

    抛出:
        ClassificationError — LLM 返回格式不正确
        ValueError           — api_key 未设置
        requests.RequestException — 网络错误

    >>> labels = classify(items, "筛选真实用户", api_key="sk-xxx")
    >>> labels = classify(comments, goal, parent=item, api_key="sk-xxx")
    """
    if not items:
        return []

    prompt = _build_prompt(items, goal, parent)
    raw = call_deepseek(
        [
            {"role": "system", "content": "你是一个信息过滤助手。只回复 JSON 数组。"},
            {"role": "user", "content": prompt},
        ],
        api_key=api_key,
        max_tokens=len(items) * 15 + 20,
    )
    arr = parse_llm_json(raw)
    if isinstance(arr, list) and len(arr) == len(items):
        return [normalize_label(str(x)) for x in arr]
    raise ClassificationError(
        f"LLM 返回格式错误: 期望 {len(items)} 个标签, "
        f"实际 {type(arr).__name__}"
    )


# ------------------------------------------------------------------
# Prompt 构建 (内部)
# ------------------------------------------------------------------


def _build_prompt(items: list[dict], goal: str, parent: dict | None = None) -> str:
    """格式化 items 为编号文本块。"""
    if parent is not None:
        return _build_comment_prompt(items, goal, parent)

    blocks = []
    for i, it in enumerate(items):
        content = it.get("content", {})
        meta = it.get("meta", {})
        author = it.get("author", "?")
        text = content.get("title", "") or content.get("body", "")

        lines = [f"[{i+1}] @{author}: {text[:300]}"]

        parts = []
        if meta.get("views"):
            parts.append(f"播放{meta['views']:,}")
        parts.append(f"赞{meta.get('likes', 0):,}")
        parts.append(f"评{meta.get('replies', 0):,}")
        lines.append(f"    {' | '.join(parts)}")

        if content.get("body"):
            lines.append(f"    {content['body'][:200]}")

        for ci, c in enumerate((it.get("comments") or [])[:3]):
            lines.append(
                f"    评{ci+1}: @{c.get('author', '?')}: {c.get('content', '')[:120]}"
            )

        blocks.append("\n".join(lines))

    return (
        f"判断以下每条内容是否与筛选目标相关。返回 JSON 数组。\n\n"
        f"筛选目标：{goal}\n\n"
        + "\n---\n".join(blocks)
        + f"\n---\n\n"
        f"逐条判断, 仅回复 JSON 数组:\n"
        f'["TARGET", "AD", "IRRELEVANT", ...]\n'
        f"数组长度 = {len(items)}。"
    )


def _build_comment_prompt(comments: list[dict], goal: str, parent: dict) -> str:
    """格式化评论列表。"""
    parent_author = parent.get("author", "?")
    parent_content = parent.get("content", {})
    parent_text = parent_content.get("title", "") or parent_content.get("body", "")

    blocks = []
    for i, c in enumerate(comments):
        blocks.append(
            f"[{i+1}] @{c.get('author', '?')}: {c.get('content', '')[:250]}"
        )

    return (
        f"判断以下评论是否与筛选目标相关。返回 JSON 数组。\n\n"
        f"筛选目标：{goal}\n"
        f"原帖 @{parent_author}: {parent_text[:200]}\n\n"
        + "\n".join(blocks)
        + f"\n\n"
        f"逐条判断, 仅回复 JSON 数组:\n"
        f'["TARGET", "AD", "IRRELEVANT", ...]\n'
        f"数组长度 = {len(comments)}。"
    )


# ------------------------------------------------------------------
# 标签归一化
# ------------------------------------------------------------------


def normalize_label(raw: str) -> str:
    """把 LLM 返回文本归一化为 TARGET/AD/IRRELEVANT。"""
    s = (raw or "").upper()
    if "TARGET" in s:
        return "TARGET"
    if "AD" in s:
        return "AD"
    return "IRRELEVANT"


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


def parse_comment_labels(raw: str, expected_len: int) -> list[str]:
    """解析评论批量分类的 JSON 数组,长度不匹配时补 IRRELEVANT。"""
    arr = parse_llm_json(raw)
    if not isinstance(arr, list):
        return ["ERROR"] * expected_len
    result = [normalize_label(str(arr[i])) if i < len(arr) else "IRRELEVANT"
              for i in range(expected_len)]
    return result


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
