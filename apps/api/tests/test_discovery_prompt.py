from app.services.discovery_prompt import (
    DEFAULT_DISCOVERY_ROLE_PROMPT,
    build_prompt_config,
    resolve_discovery_role_prompt,
)


def test_resolve_discovery_role_prompt_falls_back_to_default():
    assert resolve_discovery_role_prompt(None) == DEFAULT_DISCOVERY_ROLE_PROMPT
    assert resolve_discovery_role_prompt("  自定义  ") == "自定义"


def test_build_prompt_config_marks_custom():
    config = build_prompt_config("我的荐基角色")
    assert config.is_custom is True
    assert config.role_prompt == "我的荐基角色"

    default = build_prompt_config(None)
    assert default.is_custom is False
    assert default.role_prompt == DEFAULT_DISCOVERY_ROLE_PROMPT
