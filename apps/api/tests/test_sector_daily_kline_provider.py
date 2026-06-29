from __future__ import annotations

from app.services.sector_canonical import CanonicalSector
from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series


def test_index_canonical_uses_index_history_before_board_kline(monkeypatch):
    canon = CanonicalSector(
        label="创新药",
        source_type="index",
        source_name="创新药",
        eastmoney_secid="2.931152",
        source_code="931152",
    )

    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_eastmoney_daily_kline_series",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("eastmoney should be skipped")),
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_daily_kline_via_relay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("relay should be skipped")),
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_index_daily_via_sina",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_index_daily_via_akshare",
        lambda *_args, **_kwargs: {
            "data": [
                {"date": "2026-06-01", "close": 100},
                {"date": "2026-06-02", "close": 102},
            ]
        },
    )

    rows = fetch_canonical_daily_kline_series(canon, max_days=20, allow_akshare=True)

    assert rows[-1]["date"] == "2026-06-02"
    assert rows[-1]["close"] == 102
    assert rows[-1]["change_percent"] == 2.0
