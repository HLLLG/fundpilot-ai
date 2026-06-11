from app.services.sector_intraday_summary import _summarize_points


def test_summarize_intraday_pullback_pattern():
    points = [{"time": "09:35", "percent": 0.5}]
    for hour, pct in [("10:00", 2.8), ("11:00", 3.2), ("14:00", 1.5), ("15:00", 1.0)]:
        points.append({"time": hour, "percent": pct})
    summary = _summarize_points(points, 1.0)
    assert summary["pattern_label"] == "intraday_pullback"
    assert summary["pullback_from_high_percent"] >= 2.0
