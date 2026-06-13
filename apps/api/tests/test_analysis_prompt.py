from app.services.analysis_prompt import (
    DEFAULT_ROLE_PROMPT,
    build_prompt_config,
    normalize_role_prompt,
    resolve_role_prompt,
)
from app.services.deepseek_client import _system_prompt


def test_resolve_role_prompt_uses_default_when_empty():
    assert resolve_role_prompt(None) == DEFAULT_ROLE_PROMPT
    assert resolve_role_prompt("   ") == DEFAULT_ROLE_PROMPT


def test_resolve_role_prompt_trims_custom():
    assert resolve_role_prompt("  自定义角色  ") == "自定义角色"


def test_normalize_role_prompt_caps_length():
    long_text = "a" * 5000
    assert len(normalize_role_prompt(long_text) or "") == 4000


def test_build_prompt_config_marks_custom():
    default_config = build_prompt_config(None)
    assert default_config.is_custom is False
    assert default_config.role_prompt == DEFAULT_ROLE_PROMPT

    custom_config = build_prompt_config("我是激进型投顾")
    assert custom_config.is_custom is True
    assert custom_config.role_prompt == "我是激进型投顾"


def test_system_prompt_includes_custom_role():
    prompt = _system_prompt(True, "conservative", "我是自定义角色设定。")
    assert prompt.startswith("我是自定义角色设定。")
    assert "当前分析时点约为" in prompt
    assert "最终回复必须是完整 JSON" in prompt


def test_system_prompt_appends_tactical_suffix():
    prompt = _system_prompt(True, "tactical", None)
    assert "场外基金持仓" in prompt
    assert "战术短线模式" in prompt
