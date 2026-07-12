from app.services.fund_universe_sampler import (
    canonical_portfolio_name,
    dedupe_share_classes,
    stratified_sample_universe,
    universe_coverage,
)


def _row(code: str, name: str, fund_type: str, rank: int) -> dict:
    return {
        "fund_code": code,
        "fund_name": name,
        "fund_type": fund_type,
        "return_1y_percent": 100 - rank,
        "established_date": f"20{rank % 20:02d}-01-01",
    }


def test_share_class_names_are_normalized_and_a_is_preferred() -> None:
    rows = [
        _row("000002", "示例成长混合C", "hh", 2),
        _row("000001", "示例成长混合A", "hh", 1),
        _row("000003", "海外制造(QDII)A类", "qdii", 3),
    ]
    assert canonical_portfolio_name("示例成长混合 C") == "示例成长混合"
    assert canonical_portfolio_name("海外制造(QDII)A类") == "海外制造(QDII)"
    deduped = dedupe_share_classes(rows)
    assert [row["fund_code"] for row in deduped] == ["000001", "000003"]


def test_stratified_sample_keeps_every_available_type_and_spans_ranks() -> None:
    rows = []
    code = 1
    for fund_type, count in (("hh", 240), ("zq", 120), ("qdii", 40)):
        for rank in range(count):
            rows.append(_row(f"{code:06d}", f"{fund_type}基金{rank}A", fund_type, rank))
            code += 1
    sampled = stratified_sample_universe(rows, 180)
    by_type = {}
    for row in sampled:
        by_type.setdefault(row["fund_type"], []).append(row)
    assert set(by_type) == {"hh", "zq", "qdii"}
    assert len(sampled) == 180
    assert min(row["return_1y_percent"] for row in by_type["hh"]) < 0
    assert max(row["return_1y_percent"] for row in by_type["hh"]) > 90


def test_coverage_reports_share_class_and_portfolio_grain() -> None:
    rows = [
        _row("000001", "同一组合A", "gp", 1),
        _row("000002", "同一组合C", "gp", 2),
        _row("000003", "另一个组合A", "zq", 3),
    ]
    sampled = stratified_sample_universe(rows, 10)
    coverage = universe_coverage(rows, sampled)
    assert coverage["source_share_classes"] == 3
    assert coverage["unique_portfolios"] == 2
    assert coverage["sampled_portfolios"] == 2
    assert coverage["unique_by_type"] == {"gp": 1, "zq": 1}
