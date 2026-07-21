from __future__ import annotations

from types import SimpleNamespace

from app.services import sector_quote_diagnostic as diagnostic


def _settings(*, browser_enabled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        sector_quotes_relay_url=None,
        sector_quotes_browser_enabled=browser_enabled,
        sector_quotes_browser_command=(
            "node scripts/sector-quote-browser-command.mjs"
            if browser_enabled
            else None
        ),
    )


def _rich_intraday_points(*, scale: float = 1.0) -> list[dict[str, str | float]]:
    return [
        {
            "time": f"10:{index:02d}",
            "percent": round((0.2 + index / 100) * scale, 4),
        }
        for index in range(30)
    ]


def _stub_spot_probes(monkeypatch, *, browser_enabled: bool = False) -> None:
    monkeypatch.setattr(
        diagnostic,
        "get_settings",
        lambda: _settings(browser_enabled=browser_enabled),
    )
    monkeypatch.setattr(diagnostic, "_PROBE_SECIDS", [("中证电网设备", "2.931994")])
    monkeypatch.setattr(
        diagnostic,
        "fetch_eastmoney_boards",
        lambda **_kwargs: {
            "concept": {f"概念{index}": float(index) for index in range(8)},
            "industry": {},
            "index": {},
        },
    )
    monkeypatch.setattr(
        diagnostic,
        "fetch_eastmoney_quotes_by_secid",
        lambda *_args, **_kwargs: {
            "2.931994": {
                "security_name": "中证电网设备",
                "change_percent": 1.25,
            }
        },
    )
    monkeypatch.setattr(
        diagnostic,
        "fetch_boards_via_browser_command",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        diagnostic,
        "fetch_boards_via_akshare",
        lambda **_kwargs: {},
    )


def test_diagnostic_reports_spot_and_direct_intraday_capabilities(monkeypatch) -> None:
    _stub_spot_probes(monkeypatch)
    monkeypatch.setattr(
        diagnostic,
        "fetch_eastmoney_intraday_trends",
        lambda *_args, **_kwargs: _rich_intraday_points(),
    )

    result = diagnostic.run_sector_quote_diagnostic(timeout_seconds=2.0)

    assert result["ok"] is True
    assert result["capabilities"] == {"spot": True, "intraday": True}
    assert result["intraday_ok_paths"] == ["eastmoney_intraday"]
    intraday_probe = next(
        probe for probe in result["probes"] if probe["name"] == "eastmoney_intraday"
    )
    assert intraday_probe["entry_count"] == 30
    assert intraday_probe["sample"]["max_abs_percent"] == 0.49
    assert intraday_probe["target"]["secid"] == "2.931994"


def test_diagnostic_rejects_fraction_scale_intraday_data(monkeypatch) -> None:
    _stub_spot_probes(monkeypatch)
    monkeypatch.setattr(
        diagnostic,
        "fetch_eastmoney_intraday_trends",
        lambda *_args, **_kwargs: _rich_intraday_points(scale=0.01),
    )

    result = diagnostic.run_sector_quote_diagnostic(timeout_seconds=2.0)

    assert result["ok"] is False
    assert result["capabilities"] == {"spot": True, "intraday": False}
    intraday_probe = next(
        probe for probe in result["probes"] if probe["name"] == "eastmoney_intraday"
    )
    assert intraday_probe["ok"] is False
    assert "fractional" in intraday_probe["error"]
    assert result["recommendation"].startswith("intraday_failed:")


def test_intraday_probe_rejects_sparse_skeleton_points() -> None:
    probe = diagnostic._intraday_probe_result(
        name="eastmoney_intraday",
        start=None,
        points=[
            {"time": "09:31", "percent": -0.8},
            {"time": "15:00", "percent": 1.25},
        ],
    )

    assert probe["ok"] is False
    assert probe["entry_count"] == 2
    assert probe["error"] == "point_count below 30"


def test_diagnostic_accepts_browser_intraday_fallback(monkeypatch) -> None:
    _stub_spot_probes(monkeypatch, browser_enabled=True)
    monkeypatch.setattr(
        diagnostic,
        "fetch_eastmoney_intraday_trends",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        diagnostic,
        "fetch_intraday_via_browser_command",
        lambda *_args, **_kwargs: _rich_intraday_points(),
    )

    result = diagnostic.run_sector_quote_diagnostic(timeout_seconds=2.0)

    assert result["ok"] is True
    assert result["capabilities"] == {"spot": True, "intraday": True}
    assert result["intraday_ok_paths"] == ["browser_intraday"]
    assert result["recommendation"].startswith("browser_intraday_ok:")
