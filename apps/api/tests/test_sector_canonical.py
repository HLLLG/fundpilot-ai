def test_fetch_canonical_commercial_aerospace(monkeypatch):
    from app.services import sector_canonical as mod

    monkeypatch.setattr(
        mod,
        "fetch_eastmoney_kline_close_percent",
        lambda secid, **kwargs: 3.88 if secid == "90.BK0963" else None,
    )
    boards: dict[str, dict[str, float]] = {"concept": {}, "industry": {}, "index": {}}
    result = mod.fetch_canonical_sector_quote("商业航天", boards)
    assert result is not None
    assert result.change_percent == 3.88
    assert boards["concept"]["商业航天"] == 3.88


def test_fetch_canonical_semiconductor_uses_csi_index_for_quote(monkeypatch):
    from app.services import sector_canonical as mod

    calls: list[str] = []

    def fake_kline(secid, **kwargs):
        calls.append(secid)
        return 3.37 if secid == "2.931865" else 4.35

    monkeypatch.setattr(mod, "fetch_eastmoney_kline_close_percent", fake_kline)
    boards: dict[str, dict[str, float]] = {"concept": {}, "industry": {}, "index": {}}
    result = mod.fetch_canonical_sector_quote("半导体", boards)
    assert result is not None
    assert result.change_percent == 3.37
    assert result.source_code == "931865"
    assert calls == ["2.931865"]
    assert boards["index"]["中证半导体"] == 3.37


def test_intraday_canonical_maps_semiconductor_board_to_csi_index():
    from app.services.sector_canonical import get_intraday_canonical_sector

    canon = get_intraday_canonical_sector("半导体")
    assert canon is not None
    assert canon.source_type == "index"
    assert canon.source_code == "931865"
    assert canon.eastmoney_secid == "2.931865"


def test_get_canonical_sector_cpo_and_pcb():
    from app.services.sector_canonical import get_canonical_sector

    cpo = get_canonical_sector("CPO")
    assert cpo is not None
    assert cpo.source_code == "BK1128"
    assert cpo.source_name == "CPO概念"

    pcb = get_canonical_sector("PCB")
    assert pcb is not None
    assert pcb.source_code == "BK0877"


def test_list_discovery_sector_labels_includes_cpo_and_pcb():
    from app.services.sector_canonical import list_discovery_sector_labels

    labels = list_discovery_sector_labels()
    assert "CPO" in labels
    assert "PCB" in labels
    assert len(labels) == 21


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
        "fetch_eastmoney_kline_close_percent",
        lambda secid, **kwargs: 4.12 if secid == "90.BK0963" else None,
    )
    boards = {"concept": {}, "industry": {}, "index": {}}
    result = resolve_sector_quote("商业航天", boards)
    assert result.confidence == "high"
    assert result.change_percent == 4.12
    assert result.source_code == "BK0963"
