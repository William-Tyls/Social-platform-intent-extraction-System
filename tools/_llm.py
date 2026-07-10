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
    client: object | None = None,
) -> list[str]:
    """构建 prompt → 调 API → 返回标签列表。

    ``parent`` 为 None 时分类帖子/视频，指定时分类评论。
    ``client`` 为可选的外部 OpenAI 客户端（llm_filter.py CLI 使用）。
    """
    if not items:
        return []

    # 构建编号文本块
    blocks = []
    for i, it in enumerate(items):
        author = it.get("author", "?")
        content = it.get("content", {})
        meta = it.get("meta", {})

        if parent is not None:
            # 评论模式: content 是纯文本字符串 {"author": ..., "content": "..."}
            c_text = content if isinstance(content, str) else content.get("content", "") or ""
            blocks.append(f"[{i+1}] @{author}: {c_text[:250]}")
        else:
            # 帖子/视频模式: author + title/body + stats + 前3条评论
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

            for ci, c in enumerate(
                (it.get("comments") or [])[:3]
            ):
                lines.append(
                    f"    评{ci+1}: @{c.get('author', '?')}: {c.get('content', '')[:120]}"
                )

            blocks.append("\n".join(lines))

    # 拼装最终 prompt
    if parent is not None:
        parent_author = parent.get("author", "?")
        parent_text = (
            parent.get("content", {}).get("title", "")
            or parent.get("content", {}).get("body", "")
        )[:200]
        header = (
            f"判断以下评论是否与筛选目标相关。返回 JSON 数组。\n\n"
            f"筛选目标：{goal}\n"
            f"原帖 @{parent_author}: {parent_text}\n\n"
        )
    else:
        header = (
            f"判断以下每条内容是否与筛选目标相关。返回 JSON 数组。\n\n"
            f"筛选目标：{goal}\n\n"
        )

    prompt = (
        header
        + ("\n---\n".join(blocks) if parent is None else "\n".join(blocks))
        + f"\n\n"
        f"逐条判断, 仅回复 JSON 数组:\n"
        f'["TARGET", "AD", "IRRELEVANT", ...]\n'
        f"数组长度 = {len(items)}。"
    )

    # 调 API
    if client is not None:
        resp = client.chat.completions.create(  # type: ignore[union-attr]
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个信息过滤助手。只回复 JSON 数组。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=len(items) * 15 + 20,
        )
        raw = resp.choices[0].message.content.strip()  # type: ignore[union-attr]
    else:
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
