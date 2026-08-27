"""`prompt_version` 的归因来源：真实出处优先，缺席时如实标「无从得知」。

回归背景：`decision_contract` 里曾有两个看起来像"实时模板号镜像"的常量——
`ANALYSIS_PROMPT_VERSION = "analysis_prompt.2026-07.v4"` 与
`DISCOVERY_PROMPT_VERSION = "discovery_prompt.2026-07.v4"`。荐基模板早已走到
`discovery_prompt.2026-08.v17`，所以后者读起来就是"忘了同步"。

但把它们同步到实时模板是**错的**：`_prompt_version()` 只在报告没有冻结 `prompt_contract`
时才回落到这里（A2 之前的历史报告）。给一份根本没记录 prompt 出处的报告贴上今天的模板号是伪造
归因，还会让两个真正不同的 variant 在 `variant_hash` 上撞成同一个。

本文件把正确语义锁住，特别是"不要同步"这一条——它反直觉，只有测试能拦住善意的修复。
"""
from __future__ import annotations

import pytest

from app.services.analysis_prompt import ANALYSIS_PROMPT_TEMPLATE_VERSION
from app.services.decision_contract import (
    ANALYSIS_PROMPT_VERSION_FALLBACK,
    DISCOVERY_PROMPT_VERSION_FALLBACK,
    _prompt_version,
)
from app.services.discovery_prompt import DISCOVERY_PROMPT_TEMPLATE_VERSION

#: 回填脚本对同一语义已经在用的哨兵，两处必须一致，否则同一件事有两种写法。
_BACKFILL_SENTINEL = "unknown_at_decision_time"


@pytest.mark.parametrize("decision_kind", ["daily", "discovery"])
def test_recorded_provenance_always_wins(decision_kind: str) -> None:
    """有冻结出处时必须用真实模板号，兜底常量压根不参与。"""
    assert (
        _prompt_version({"template_version": "some_prompt.2026-09.v42"}, decision_kind)
        == "some_prompt.2026-09.v42"
    )


@pytest.mark.parametrize("decision_kind", ["daily", "discovery"])
@pytest.mark.parametrize(
    "contract",
    [None, {}, {"template_version": ""}, {"template_version": "   "}, "not-a-dict"],
)
def test_absent_provenance_reports_unknown(contract, decision_kind: str) -> None:
    assert _prompt_version(contract, decision_kind) == _BACKFILL_SENTINEL


def test_fallback_matches_the_backfill_sentinel() -> None:
    """与 scripts/backfill_decision_events_v2.py 的 prompt_version 对齐。"""
    assert ANALYSIS_PROMPT_VERSION_FALLBACK == _BACKFILL_SENTINEL
    assert DISCOVERY_PROMPT_VERSION_FALLBACK == _BACKFILL_SENTINEL


def test_backfill_script_still_uses_the_same_sentinel() -> None:
    """脚本换了写法就必须同步这里，否则同一语义又分叉成两个字符串。"""
    from pathlib import Path

    from app.config import PROJECT_ROOT

    script = PROJECT_ROOT / "scripts" / "backfill_decision_events_v2.py"
    if not script.exists():  # pragma: no cover - 脚本随仓库一起提供
        pytest.skip("backfill script not present in this checkout")
    source = Path(script).read_text(encoding="utf-8")
    assert f'"prompt_version": "{_BACKFILL_SENTINEL}"' in source


def test_fallback_must_not_track_the_live_template_constants() -> None:
    """**不要**把兜底常量改成实时模板号。

    这条断言是反直觉的，所以写清原因：兜底只在"报告没有冻结 prompt 出处"时使用。把它接到
    实时模板上等于宣称那份报告用了今天的模板——伪造归因，且会让不同 variant 的
    `variant_hash` 撞车。带出处的报告走 `prompt_contract`，永远到不了这里。
    """
    assert ANALYSIS_PROMPT_VERSION_FALLBACK != ANALYSIS_PROMPT_TEMPLATE_VERSION
    assert DISCOVERY_PROMPT_VERSION_FALLBACK != DISCOVERY_PROMPT_TEMPLATE_VERSION


def test_fallback_is_not_a_plausible_template_number() -> None:
    """兜底值不得长得像某一版模板号，否则又会被误读成"停在旧版本"。"""
    for value in (ANALYSIS_PROMPT_VERSION_FALLBACK, DISCOVERY_PROMPT_VERSION_FALLBACK):
        assert "prompt." not in value
        assert not any(token in value for token in (".v", "2026-", "2027-"))
