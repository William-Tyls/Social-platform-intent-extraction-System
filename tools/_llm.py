"""LLM 过滤共享模块 — console.py 和 llm_filter.py 共用。

提供:
  - build_classify_prompt: 唯一的分类 prompt 函数
  - call_deepseek: DeepSeek chat completions
  - normalize_label / parse_llm_json / parse_comment_labels: 结果解析

用法:
    from _llm import build_classify_prompt, call_deepseek

    # 批量分类帖子/视频
    prompt = build_classify_prompt(items, "筛选目标")

    # 分类评论 (指定 parent 提供原帖上下文)
    prompt = build_classify_prompt(comments, "筛选目标", parent=item)

环境变量: DEEPSEEK_API_KEY
"""

from __future__ import annotations

import json
import os
import time

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

_PLATFORM_LABEL = {"twitter": "推文", "reddit": "帖子", "youtube": "视频"}

# ------------------------------------------------------------------
# 唯一的分类 Prompt
# ------------------------------------------------------------------


def build_classify_prompt(
    items: list[dict],
    goal: str,
    *,
    parent: dict | None = None,
) -> str:
    """构建分类 prompt，要求 LLM 返回等长 JSON 标签数组。

    接受归一化后的 item 列表。``parent`` 为 None 时分类帖子/视频，
    指定时分类评论（附带原帖上下文）。

    item 格式:
        {id, platform, author, content: {title, body}, meta: {...},
         comments: [{author, content}, ...], ...}

    用法:
        # 批量分类
        prompt = build_classify_prompt(all_items, "筛选真实用户")

        # 逐条回退
        prompt = build_classify_prompt([item], "筛选真实用户")

        # 评论分类
        prompt = build_classify_prompt(item["comments"], goal, parent=item)
    """
    if parent is not None:
        return _build_prompt_comments(items, goal, parent)
    return _build_prompt_items(items, goal)


def _build_prompt_items(items: list[dict], goal: str) -> str:
    """构建帖子/视频分类 prompt。"""
    blocks = []
    for i, it in enumerate(items):
        content = it.get("content", {})
        meta = it.get("meta", {})
        author = it.get("author", "?")
        platform = _PLATFORM_LABEL.get(it.get("platform", ""), "内容")

        text = content.get("title", "") or content.get("body", "")

        # 互动数据 (YouTube 用播放量, 其他用赞/评)
        if it.get("platform") == "youtube":
            stats = f"播放{meta.get('views', 0):,} | 赞{meta.get('likes', 0):,} | 评{meta.get('replies', 0):,}"
        else:
            stats = f"赞{meta.get('likes', 0):,} | 评{meta.get('replies', 0):,}"

        lines = [f"[{i+1}] 【{platform}】@{author}: {text[:300]}"]
        if text or stats:
            lines.append(f"    {stats}")

        # YouTube 摘要
        if it.get("platform") == "youtube" and content.get("body"):
            lines.append(f"    摘要: {content['body'][:200]}")

        # 前 3 条评论作为上下文
        comments = it.get("comments") or []
        for ci, c in enumerate(comments[:3]):
            c_author = c.get("author", "?")
            c_text = c.get("content", "")[:120]
            lines.append(f"    评{ci+1}: @{c_author}: {c_text}")

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


def _build_prompt_comments(comments: list[dict], goal: str, parent: dict) -> str:
    """构建评论分类 prompt。"""
    parent_author = parent.get("author", "?")
    parent_content = parent.get("content", {})
    parent_text = parent_content.get("title", "") or parent_content.get("body", "")

    blocks = []
    for i, c in enumerate(comments):
        blocks.append(f"[{i+1}] @{c.get('author', '?')}: {c.get('content', '')[:250]}")

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
