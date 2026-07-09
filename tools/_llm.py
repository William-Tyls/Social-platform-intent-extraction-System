"""LLM 过滤共享模块 — console.py 和 llm_filter.py 共用。

提供:
  - Prompt 构建: 推文/评论分类提示词 (统一归一化 schema)
  - API 调用: DeepSeek chat completions
  - 结果解析: 标签归一化、JSON 数组解析

所有 prompt 函数均接受 _normalize.py 产出的统一格式::

    {id, platform, author, author_name, content: {title, body}, meta: {...},
     comments: [{author, content}, ...], profile: {...}}

用法:
    from _llm import build_classify_prompt, build_unified_batch_prompt, call_deepseek

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
# Prompt 构建 (统一归一化格式)
# ------------------------------------------------------------------


def build_classify_prompt(item: dict, goal: str) -> str:
    """单条分类提示词 — 接受归一化 item。

    读取 item.content.title、item.content.body、item.comments[].author/content、
    item.meta.*、item.profile。无字段猜测、无兜底链。
    """
    content = item.get("content", {})
    meta = item.get("meta", {})
    platform = item.get("platform", "")
    kind = _PLATFORM_LABEL.get(platform, "推文")

    # 评论
    comments = item.get("comments") or []
    if comments:
        parts = [
            f"    评论{ci+1}: @{c.get('author', '?')}: {c.get('content', '')[:200]}"
            for ci, c in enumerate(comments[:10])
        ]
        comments_block = "\n".join(parts)
    else:
        comments_block = "  (无评论)"

    # 帖主/频道信息
    profile = item.get("profile")
    if profile:
        profile_text = (
            f"  bio: {profile.get('bio', '')[:200]}\n"
            f"  粉丝: {profile.get('followers', 0)}\n"
            f"  关注: {profile.get('following', 0)}"
        )
    else:
        body = content.get("body", "")
        profile_text = f"  {body[:200]}" if body else "  (未采集)"

    text = content.get("title", "") or content.get("body", "")

    return f"""判断这条{kind}是否为目标信息。

筛选目标：{goal}

{kind}内容：
  作者: @{item.get('author', '')} ({item.get('author_name', '')})
  正文: {text[:500]}
  互动: 赞{meta.get('likes', 0)}  评论{meta.get('replies', 0)}

作者信息：
{profile_text}

评论：
{comments_block}

请仅回复以下之一（不要多余文字）：
[TARGET] — 符合筛选目标
[AD] — 广告、推广、营销
[IRRELEVANT] — 无关"""


def build_comment_batch_prompt(comments: list[dict], parent: dict, goal: str) -> str:
    """评论批量分类提示词 — 接受归一化格式的 comments 和 parent 项。

    comments: [{"author": ..., "content": ...}, ...]
    parent:   归一化 item, 含 author, content.title
    """
    parts = []
    for ci, c in enumerate(comments):
        handle = c.get("author", "?")
        parts.append(
            f"[{ci+1}] @{handle}:\n    {c.get('content', '')[:300]}"
        )

    parent_content = parent.get("content", {})
    parent_text = parent_content.get("title", "")

    return f"""判断以下每条评论是否为目标信息。回复 JSON 数组。

筛选目标：{goal}

原帖: @{parent.get('author', '?')}: {parent_text[:200]}

评论列表：
{"\n\n".join(parts)}

请逐条判断,仅回复 JSON 数组（不要其他文字）:
["TARGET", "IRRELEVANT", "AD", "IRRELEVANT", ...]
数组长度必须等于评论数 ({len(comments)} 条)。
TARGET=符合筛选目标，AD=广告/推广，IRRELEVANT=无关"""


def build_unified_batch_prompt(items: list[dict], goal: str) -> str:
    """批量分类 prompt — 接受归一化 item 列表。

    将 item.content.title/body 和 item.comments[].author/content 打包为
    编号列表，要求 LLM 一次返回等长 JSON 标签数组。
    """
    lines = []
    for i, it in enumerate(items):
        content = it.get("content", {})
        author = it.get("author", "?")
        title = content.get("title", "")
        body = content.get("body", "")
        text = title or body or ""
        platform_tag = _PLATFORM_LABEL.get(it.get("platform", ""), "内容")

        lines.append(f"[{i+1}] 【{platform_tag}】@{author}: {text[:300]}")

        comments = it.get("comments") or []
        if comments:
            for ci, c in enumerate(comments[:3]):
                c_author = c.get("author", "?")
                c_text = c.get("content", "")[:150]
                lines.append(f"    评论{ci+1}: @{c_author}: {c_text}")

    return (
        f"判断以下每条是否为目标信息。逐条回复 JSON 数组。\n\n"
        f"筛选目标：{goal}\n\n"
        f"列表：\n"
        f'{"\n".join(lines)}\n\n'
        f"请逐条判断,仅回复 JSON 数组(不要其他文字):\n"
        f'["TARGET", "AD", "IRRELEVANT", ...]\n'
        f"数组长度必须等于总数 ({len(items)} 条)。\n"
        f"TARGET=符合筛选目标,AD=广告/推广,IRRELEVANT=无关"
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
