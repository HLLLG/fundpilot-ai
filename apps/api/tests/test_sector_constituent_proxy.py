from app.services.sector_constituent_proxy import build_constituent_proxy_series


def test_constituent_proxy_uses_market_cap_weights_and_common_dates():
    loaded = []
    for member_index, market_cap in enumerate((40, 30, 20, 10)):
        rows = [
            {
                "date": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}",
                "close": 100 + day * (member_index + 1),
            }
            for day in range(1, 65)
        ]
        loaded.append(({"code": str(member_index), "market_cap": market_cap}, rows))

    result = build_constituent_proxy_series(loaded, max_days=100)

    assert len(result) == 64
    assert result[0]["close"] == 100.0
    assert result[-1]["close"] > result[0]["close"]
    assert result[-1]["_proxy_member_count"] == 4
    assert result[-1]["_source"] == "sina_current_large_constituents_proxy"


def test_constituent_proxy_fails_closed_with_too_few_long_histories():
    loaded = [
        (
            {"code": str(index), "market_cap": 10},
            [{"date": f"2026-01-{day:02d}", "close": 100 + day} for day in range(1, 21)],
        )
        for index in range(4)
    ]

    assert build_constituent_proxy_series(loaded, max_days=100) == []
