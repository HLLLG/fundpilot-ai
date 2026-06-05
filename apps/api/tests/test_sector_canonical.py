def test_fetch_canonical_commercial_aerospace(monkeypatch):
    from app.services import sector_canonical as mod

    monkeypatch.setattr(
        mod,
        "fetch_eastmoney_quote_by_secid",
        lambda secid, **kwargs: ("商业航天", 3.88) if secid == "90.BK0963" else (None, None),
    )
    boards: dict[str, dict[str, float]] = {"concept": {}, "industry": {}, "index": {}}
    result = mod.fetch_canonical_sector_quote("商业航天", boards)
    assert result is not None
    assert result.change_percent == 3.88
    assert boards["concept"]["商业航天"] == 3.88


def test_intraday_canonical_maps_semiconductor_board_to_csi_index():
    from app.services.sector_canonical import get_intraday_canonical_sector

    canon = get_intraday_canonical_sector("半导体")
    assert canon is not None
    assert canon.source_type == "index"
    assert canon.source_code == "931865"
    assert canon.eastmoney_secid == "2.931865"


def test_fuzzy_match_blocks_wrong_aerospace_name():
    from app.services.sector_quote_resolver import _fuzzy_sector_match

    assert _fuzzy_sector_match("商业航天", "商业航天")
    assert not _fuzzy_sector_match("商业航天", "航天装备")
    assert not _fuzzy_sector_match("商业航天", "卫星导航")


def test_resolve_commercial_aerospace_uses_canonical(monkeypatch):
    from app.services import sector_canonical as canon_mod
    from app.services.sector_quote_resolver import resolve_sector_quote

    monkeypatch.setattr(
        canon_mod,
        "fetch_eastmoney_quote_by_secid",
        lambda secid, **kwargs: ("商业航天", 4.12) if secid == "90.BK0963" else (None, None),
    )
    boards = {"concept": {}, "industry": {}, "index": {}}
    result = resolve_sector_quote("商业航天", boards)
    assert result.confidence == "high"
    assert result.change_percent == 4.12
    assert result.source_code == "BK0963"
