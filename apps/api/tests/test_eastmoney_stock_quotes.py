from app.services.eastmoney_spot_client import _parse_eastmoney_quote_rows


def test_parse_eastmoney_quote_rows_preserves_market_namespace_and_timestamp() -> None:
    result = _parse_eastmoney_quote_rows(
        [
            {
                "f2": 19.5,
                "f3": 1.56,
                "f12": "600089",
                "f13": 1,
                "f14": "特变电工",
                "f124": 1784621497,
            },
            {
                "f2": 474.0,
                "f3": -0.8,
                "f12": "00700",
                "f13": 116,
                "f14": "腾讯控股",
                "f124": 1784621284,
            },
        ],
        requested={"1.600089", "116.00700"},
    )

    assert result["1.600089"]["change_percent"] == 1.56
    assert result["1.600089"]["quote_timestamp"] == 1784621497
    assert result["116.00700"]["change_percent"] == -0.8
    assert result["116.00700"]["security_name"] == "腾讯控股"


def test_parse_eastmoney_quote_rows_ignores_unrequested_and_malformed_rows() -> None:
    result = _parse_eastmoney_quote_rows(
        [
            {"f12": "600089", "f13": 1, "f3": 2.0, "f124": 1784621497},
            {"f12": "002028", "f13": 0, "f3": 2.9, "f124": 1784619261},
            "not-a-row",
        ],
        requested={"0.002028"},
    )

    assert list(result) == ["0.002028"]
    assert result["0.002028"]["latest_price"] is None
