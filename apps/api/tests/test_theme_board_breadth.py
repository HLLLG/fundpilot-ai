from app.services.eastmoney_spot_client import _parse_clist_theme_rows


def test_clist_theme_metrics_include_advancing_breadth() -> None:
    parsed = _parse_clist_theme_rows(
        [
            {
                "f12": "BK1128",
                "f3": 2.5,
                "f109": 8.2,
                "f104": 30,
                "f105": 10,
                "f106": 0,
            }
        ]
    )

    row = parsed["BK1128"]
    assert row["rising_count"] == 30
    assert row["falling_count"] == 10
    assert row["flat_count"] == 0
    assert row["advancing_ratio_percent"] == 75.0
