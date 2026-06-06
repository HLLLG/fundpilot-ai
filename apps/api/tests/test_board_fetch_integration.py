"""集成测试：验证板块数据获取级联策略
测试三层策略：东财 → AkShare → 缓存
"""
from unittest.mock import MagicMock

from app.services.sector_quote_provider import fetch_spot_boards


def test_board_fetch_cascade_eastmoney_success(monkeypatch):
    """场景：东财直连成功 ✓"""
    # 确保settings启用了
    mock_settings = MagicMock()
    mock_settings.sector_quotes_enabled = True
    mock_settings.sector_quotes_ttl_seconds = 300
    monkeypatch.setattr("app.services.sector_quote_provider.get_settings", lambda: mock_settings)

    mock_boards = {
        "concept": {"芯片设计": 0.025, "AI应用": 0.015},
        "industry": {"计算机": 0.018},
        "index": {"沪深300": -0.008},
    }
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_eastmoney_boards",
        lambda **kwargs: mock_boards,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_boards_via_akshare",
        lambda **kwargs: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.get_spot_snapshot",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.save_spot_snapshot",
        lambda *a, **k: None,
    )

    result = fetch_spot_boards(force_refresh=True)
    assert result["concept"]["芯片设计"] == 0.025
    assert result["industry"]["计算机"] == 0.018


def test_board_fetch_cascade_eastmoney_fail_akshare_success(monkeypatch):
    """场景：东财失败，AkShare成功 ✓ (此处用mock模拟，实际环境会真实调用)"""
    mock_settings = MagicMock()
    mock_settings.sector_quotes_enabled = True
    mock_settings.sector_quotes_ttl_seconds = 300
    monkeypatch.setattr("app.services.sector_quote_provider.get_settings", lambda: mock_settings)

    def mock_eastmoney(**kwargs):
        raise Exception("东财网络超时或被墙")

    mock_akshare_boards = {
        "concept": {"光电芯片": 0.032},
        "industry": {"通信设备": 0.022},
        "index": {},
    }

    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_eastmoney_boards",
        mock_eastmoney,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_boards_via_akshare",
        lambda include_index=False: mock_akshare_boards,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.get_spot_snapshot",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.save_spot_snapshot",
        lambda *a, **k: None,
    )

    result = fetch_spot_boards(force_refresh=True)
    assert result["concept"]["光电芯片"] == 0.032
    assert result["industry"]["通信设备"] == 0.022


def test_board_fetch_cascade_both_fail_use_cache(monkeypatch):
    """场景：东财+AkShare都失败，使用缓存 ✓"""
    mock_settings = MagicMock()
    mock_settings.sector_quotes_enabled = True
    mock_settings.sector_quotes_ttl_seconds = 300
    monkeypatch.setattr("app.services.sector_quote_provider.get_settings", lambda: mock_settings)

    stale_cache = {
        "concept": {f"旧板块{i}": 0.005 for i in range(5)},
        "industry": {f"旧行业{i}": 0.003 for i in range(4)},
        "index": {},
    }

    def mock_eastmoney(**kwargs):
        raise Exception("东财网络错误")

    def mock_akshare(include_index=False):
        raise Exception("AkShare网络错误")

    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_eastmoney_boards",
        mock_eastmoney,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.fetch_boards_via_akshare",
        mock_akshare,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_provider.get_spot_snapshot",
        lambda cache_key, ttl_seconds=None: stale_cache if ttl_seconds == 24*3600 else None,
    )

    result = fetch_spot_boards(force_refresh=True)
    # 应该返回缓存中的旧数据
    assert result["concept"]["旧板块0"] == 0.005


def test_board_fetch_respects_ttl_without_force_refresh(monkeypatch):
    """场景：默认TTL内不刷新 ✓"""
    mock_settings = MagicMock()
    mock_settings.sector_quotes_enabled = True
    mock_settings.sector_quotes_ttl_seconds = 300
    monkeypatch.setattr("app.services.sector_quote_provider.get_settings", lambda: mock_settings)

    cached_data = {
        "concept": {f"缓存板块{i}": 0.01 for i in range(8)},
        "industry": {},
        "index": {},
    }

    get_snapshot_call_count = [0]

    def mock_get_snapshot(cache_key, ttl_seconds=None):
        get_snapshot_call_count[0] += 1
        if ttl_seconds and ttl_seconds < 1000:  # 正常TTL缓存
            return cached_data
        return None  # 24小时缓存查询

    monkeypatch.setattr(
        "app.services.sector_quote_provider.get_spot_snapshot",
        mock_get_snapshot,
    )

    result = fetch_spot_boards(force_refresh=False)
    assert result["concept"]["缓存板块0"] == 0.01
    # 应该命中缓存，不调用fetch_eastmoney_boards
