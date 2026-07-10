"""LLM 共享模块 单元测试。

覆盖 _llm.py:
  - parse_llm_json: JSON 解析
  - classify: prompt 构建 + 意向对象返回 (mock)

运行:
    python tools/tests/test_llm.py
    python -m pytest tools/tests/test_llm.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _llm import classify, ClassificationError, parse_llm_json  # noqa: E402


# --- helper mock ---

def _mock_response(intents: list[str]) -> str:
    """Builds a JSON response matching classify's expected object format."""
    return json.dumps([
        {"author": f"u{i+1}", "intent": intent, "content": f"text{i+1}"}
        for i, intent in enumerate(intents)
    ])


def _assert_prompt(items, goal, **checks):
    """Mock call_deepseek, then assert prompt content."""
    response = json.dumps([
        {"author": f"u{i+1}", "intent": "NONE", "content": f"t{i+1}"}
        for i in range(len(items))
    ])
    with patch("_llm.call_deepseek") as mock_call:
        mock_call.return_value = response
        classify(items, goal, api_key="sk-test")
        prompt = mock_call.call_args[0][0][1]["content"]
    for text, expected in checks.items():
        if expected:
            assert text in prompt, f"{text!r} not in prompt"
        else:
            assert text not in prompt, f"{text!r} in prompt"


def _comment(author="u1", content="test", pa="op", pc="post"):
    return {"author": author, "content": content,
            "parent_author": pa, "parent_content": pc}


# ====================================================================
# parse_llm_json
# ====================================================================

def test_parse_plain_array():
    assert parse_llm_json('["a", "b"]') == ["a", "b"]


def test_parse_plain_object():
    assert parse_llm_json('{"key": "value"}') == {"key": "value"}


def test_parse_markdown_json_block():
    assert parse_llm_json('```json\n["TARGET", "AD"]\n```') == ["TARGET", "AD"]


def test_parse_bare_code_fence():
    assert parse_llm_json('```\n["x", "y"]\n```') == ["x", "y"]


def test_parse_empty_string():
    assert parse_llm_json("") is None


def test_parse_none_input():
    assert parse_llm_json(None) is None


def test_parse_invalid_json():
    assert parse_llm_json("not json") is None
    assert parse_llm_json("{broken") is None


# ====================================================================
# classify — prompt
# ====================================================================

def test_prompt_includes_post_context():
    items = [_comment(pa="poster", pc="新品发布啦")]
    _assert_prompt(items, "AI 工具", poster=True, **{"新品发布啦": True})


def test_prompt_includes_comment():
    items = [_comment(author="buyer", content="怎么买")]
    _assert_prompt(items, "test", buyer=True, **{"怎么买": True})


def test_prompt_includes_goal():
    items = [_comment()]
    _assert_prompt(items, "筛选购买用户", **{"筛选购买用户": True})


def test_prompt_includes_intent_labels():
    items = [_comment()]
    _assert_prompt(items, "goal", HIGH=True, INTERESTED=True, NONE=True)


def test_prompt_multiple_items():
    items = [_comment(content="c1"), _comment(content="c2")]
    _assert_prompt(items, "x", c1=True, c2=True)


def test_prompt_empty():
    assert classify([], "goal", api_key="sk-test") == []


# ====================================================================
# classify — 返回值
# ====================================================================

def test_classify_returns_objects():
    items = [_comment()]
    with patch("_llm.call_deepseek", return_value=_mock_response(["HIGH"])):
        result = classify(items, "goal", api_key="sk-test")
    assert result == [{"author": "u1", "intent": "HIGH", "content": "text1"}]


def test_classify_normalizes_intent_cases():
    """大小写和中英文混合都能归一化。"""
    items = [_comment(), _comment(), _comment(), _comment(), _comment()]
    response = json.dumps([
        {"author": "a1", "intent": "high", "content": "t"},
        {"author": "a2", "intent": "HIGH", "content": "t"},
        {"author": "a3", "intent": "interested", "content": "t"},
        {"author": "a4", "intent": "感兴趣", "content": "t"},
        {"author": "a5", "intent": "NONE", "content": "t"},
    ])
    with patch("_llm.call_deepseek", return_value=response):
        result = classify(items, "goal", api_key="sk-test")
    assert result[0]["intent"] == "HIGH"
    assert result[1]["intent"] == "HIGH"
    assert result[2]["intent"] == "INTERESTED"
    assert result[3]["intent"] == "INTERESTED"
    assert result[4]["intent"] == "NONE"


def test_classify_bad_response_raises():
    items = [_comment()]
    with patch("_llm.call_deepseek", return_value="not json"):
        try:
            classify(items, "goal", api_key="sk-test")
            assert False, "should have raised"
        except ClassificationError:
            pass


def test_classify_wrong_length_raises():
    items = [_comment(), _comment()]
    with patch("_llm.call_deepseek", return_value='[{"author":"a","intent":"HIGH","content":"t"}]'):
        try:
            classify(items, "goal", api_key="sk-test")
            assert False, "should have raised"
        except ClassificationError:
            pass


def test_classify_returns_none_on_missing_fields():
    items = [_comment()]
    with patch("_llm.call_deepseek", return_value='[{"intent":"NONE"}]'):
        result = classify(items, "goal", api_key="sk-test")
    assert result[0]["author"] is None
    assert result[0]["content"] is None


# ---- run ----

def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
