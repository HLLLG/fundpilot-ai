from app.services.sector_registry import (
    get_sector_entry,
    list_discovery_sector_labels,
    list_theme_board_labels,
    resolve_discovery_quote,
    resolve_market_quote,
)


def test_list_discovery_sector_labels_count_and_cpo():
    labels = list_discovery_sector_labels()
    assert "CPO" in labels
    assert "PCB" in labels
    assert len(labels) >= 21


def test_list_theme_board_labels_includes_ai_and_count():
    labels = list_theme_board_labels()
    assert "人工智能" in labels
    assert len(labels) >= 60


def test_alias_military_maps_to_same_discovery_quote():
    entry_gf = get_sector_entry("国防军工")
    entry_jg = get_sector_entry("军工")
    assert entry_gf is not None
    assert entry_jg is not None
    assert resolve_discovery_quote("国防军工") == resolve_discovery_quote("军工")


def test_market_and_discovery_quotes_differ_for_ai_when_configured():
    market = resolve_market_quote("人工智能")
    discovery = resolve_discovery_quote("人工智能")
    assert market is not None and discovery is not None
    assert market.eastmoney_secid != discovery.eastmoney_secid


def test_cloud_computing_market_quote_secid():
    market = resolve_market_quote("云计算")
    assert market is not None
    assert market.source_code == "930851"
