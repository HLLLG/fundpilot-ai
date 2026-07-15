"""M4：荐基 deep 模式风控复核角色（discovery_judge.py）。"""

from __future__ import annotations

import json

from app.config import get_settings, refresh_settings
from app.services.discovery_judge import (
    _escalation_hints_by_fund_code,
    judge_parsed_discovery_report,
)


def _candidate_pool(*, fund_quality_score: float = 40.0) -> list[dict]:
    return [
        {
            "fund_code": "020357",
            "fund_name": "华夏半导体材料设备ETF联接C",
            "sector_label": "半导体材料",
            "fund_quality_score": fund_quality_score,
            "sector_fit_score": 37.12,
        }
    ]


def _discovery_facts(*, opportunity_available: bool) -> dict:
    return {
        "sector_opportunities": [
            {
                "sector_label": "半导体材料",
                "track": "momentum",
                "score": 86.5,
                "confidence": "高",
                "opportunity_available": opportunity_available,
                "penalties": ["资金背离或持续流出"] if not opportunity_available else [],
            }
        ]
    }


def _parsed(action: str = "分批买入") -> dict:
    return {
        "title": "机会扫描",
        "summary": "半导体材料方向。",
        "market_view": "偏强。",
        "recommendations": [
            {
                "fund_code": "020357",
                "fund_name": "华夏半导体材料设备ETF联接C",
                "sector_name": "半导体材料",
                "action": action,
                "suggested_amount_yuan": 5000,
                "hold_horizon": "2-4周",
                "confidence": "中",
                "points": ["近3/6月表现占优"],
                "risks": ["波动较高"],
            }
        ],
        "caveats": [],
    }


def test_fast_mode_never_attempts_llm_judge(monkeypatch):
    """M4 对齐 M3.1：fast 模式下荐基风控复核角色也应零新增 LLM 调用。"""
    monkeypatch.setattr(
        "httpx.post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应发出请求"))
    )
    parsed = _parsed()
    out, meta = judge_parsed_discovery_report(
        parsed,
        candidate_pool=_candidate_pool(),
        discovery_facts=_discovery_facts(opportunity_available=False),
        analysis_mode="fast",
    )
    assert out is parsed
    assert meta == {
        "llm_judge_attempted": False,
        "llm_judge_applied": False,
        "llm_judge_timeout": False,
    }


def test_deep_mode_without_deepseek_configured_skips_judge(monkeypatch):
    """未配置 DeepSeek key 时（settings.deepseek_configured=False），即使 analysis_mode=deep
    也应直接跳过，不尝试发请求（避免裸调用 httpx 抛不可控异常）。

    注意：不能靠 `monkeypatch.delenv` 清空环境变量来模拟"未配置"——pydantic-settings
    的 env_file 会继续从本地 `.env` 读取真实 key（本地开发环境通常配置了真实 DeepSeek
    key 用于手工验证），delenv 只是清了 OS 环境变量，实测会导致本用例误发真实网络请求。
    改为直接 patch `get_settings` 返回值的 `deepseek_configured` 字段，完全不依赖任何
    外部环境状态。"""

    class _FakeSettings:
        deepseek_configured = False

    monkeypatch.setattr(
        "app.services.discovery_judge.get_settings", lambda: _FakeSettings()
    )
    parsed = _parsed()
    out, meta = judge_parsed_discovery_report(
        parsed,
        candidate_pool=_candidate_pool(),
        discovery_facts=_discovery_facts(opportunity_available=False),
        analysis_mode="deep",
    )
    assert out is parsed
    assert meta["llm_judge_attempted"] is False


def test_escalation_hints_by_fund_code_matches_discovery_guard_logic():
    """_escalation_hints_by_fund_code 应与 discovery_guard.py 实际执行的判定完全一致
    （两处都调用同一个 resolve_discovery_escalation 入口，避免规则 guard 与 LLM 提示
    对同一只基金给出不同结论）。"""
    hints = _escalation_hints_by_fund_code(
        _candidate_pool(fund_quality_score=40.0),
        _discovery_facts(opportunity_available=False),
    )
    assert hints["020357"]["action"] == "exclude"


def test_escalation_hints_skip_funds_without_trigger():
    hints = _escalation_hints_by_fund_code(
        _candidate_pool(fund_quality_score=70.0),
        _discovery_facts(opportunity_available=False),
    )
    assert "020357" not in hints


def test_deep_mode_sends_risk_review_persona_with_escalation_hints(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "c" * 32)
    refresh_settings()

    captured: dict = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "reviewed",
                                    "summary": "已剔除弱势候选。",
                                    "market_view": "偏弱。",
                                    "recommendations": [],
                                    "caveats": [],
                                }
                            )
                        }
                    }
                ]
            }

    def fake_post(url, *, headers, json, timeout):  # noqa: A002
        captured["system"] = json["messages"][0]["content"]
        captured["user"] = json["messages"][1]["content"]
        return _FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    parsed = _parsed()
    parsed["recommendations"][0]["validation_notes"] = [
        {
            "position_snapshot": {
                "raw_events": ["DISCOVERY_DRAFT_LEDGER_LEAK_SENTINEL"]
            }
        }
    ]
    parsed["candidate_pool"] = [
        {"raw_candidate": "DISCOVERY_DRAFT_POOL_LEAK_SENTINEL"}
    ]
    candidate_pool = _candidate_pool(fund_quality_score=40.0)
    candidate_pool[0]["custom_payload"] = {
        "raw_snapshot": "DISCOVERY_JUDGE_LEAK_SENTINEL"
    }
    candidate_pool[0]["quality_gate"] = {
        "eligible": False,
        "status": "excluded",
        "reasons": ["hard_gate"],
        "custom_snapshot": "DISCOVERY_QUALITY_GATE_LEAK_SENTINEL",
    }
    candidate_pool[0]["quality_score_components"] = {
        "sector_fit": 20.0,
        "custom_component": {"raw": "DISCOVERY_QUALITY_COMPONENT_LEAK_SENTINEL"},
    }
    out, meta = judge_parsed_discovery_report(
        parsed,
        candidate_pool=candidate_pool,
        discovery_facts=_discovery_facts(opportunity_available=False),
        analysis_mode="deep",
    )

    assert meta["llm_judge_attempted"] is True
    assert meta["llm_judge_applied"] is True
    assert out["recommendations"] == []
    assert "风控经理" in captured["system"]
    user_payload = json.loads(captured["user"])
    assert user_payload["escalation_hints"]["020357"]["action"] == "exclude"
    assert "escalation_hints" in user_payload["task"]
    assert "custom_payload" not in user_payload["candidate_pool"][0]
    assert "DISCOVERY_JUDGE_LEAK_SENTINEL" not in captured["user"]
    assert "DISCOVERY_DRAFT_LEDGER_LEAK_SENTINEL" not in captured["user"]
    assert "DISCOVERY_DRAFT_POOL_LEAK_SENTINEL" not in captured["user"]
    assert "DISCOVERY_QUALITY_GATE_LEAK_SENTINEL" not in captured["user"]
    assert "DISCOVERY_QUALITY_COMPONENT_LEAK_SENTINEL" not in captured["user"]
    assert "validation_notes" not in user_payload["draft_report"]["recommendations"][0]
    assert "candidate_pool" not in user_payload["draft_report"]


def test_deep_mode_falls_back_to_draft_when_llm_response_invalid(monkeypatch):
    """LLM 返回的 JSON 里 recommendations 不是数组（或请求异常）时应静默回退到原始 draft，
    不应让整个荐基流程崩溃——与 report_judge.py 的鲁棒性设计一致。"""
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "c" * 32)
    refresh_settings()
    monkeypatch.setattr("httpx.post", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    parsed = _parsed()
    out, meta = judge_parsed_discovery_report(
        parsed,
        candidate_pool=_candidate_pool(),
        discovery_facts=_discovery_facts(opportunity_available=False),
        analysis_mode="deep",
    )
    assert out is parsed
    assert meta["llm_judge_attempted"] is True
    assert meta["llm_judge_applied"] is False


def test_deep_mode_times_out_gracefully(monkeypatch):
    import time

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "c" * 32)
    refresh_settings()
    monkeypatch.setattr("app.services.discovery_judge.LLM_JUDGE_TIMEOUT_SECONDS", 0.01)

    def slow_post(*args, **kwargs):
        time.sleep(0.2)
        raise RuntimeError("should not reach here")

    monkeypatch.setattr("httpx.post", slow_post)

    parsed = _parsed()
    start = time.monotonic()
    out, meta = judge_parsed_discovery_report(
        parsed,
        candidate_pool=_candidate_pool(),
        discovery_facts=_discovery_facts(opportunity_available=False),
        analysis_mode="deep",
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.15
    assert out is parsed
    assert meta["llm_judge_timeout"] is True
    assert meta["llm_judge_applied"] is False


def test_no_judge_when_recommendations_not_a_list(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "c" * 32)
    refresh_settings()
    parsed = {"title": "x", "recommendations": "not-a-list"}
    out, meta = judge_parsed_discovery_report(
        parsed,
        candidate_pool=_candidate_pool(),
        discovery_facts=_discovery_facts(opportunity_available=False),
        analysis_mode="deep",
    )
    assert out is parsed
    assert meta["llm_judge_attempted"] is False
