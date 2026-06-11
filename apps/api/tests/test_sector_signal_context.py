from app.models import Holding
from app.services.sector_signal_context import (
    sector_labels_from_holdings,
    signal_backtest_for_sector,
)


def test_sector_labels_from_holdings_deduplicates():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="A",
            holding_amount=1000,
            sector_name="半导体",
        ),
        Holding(
            fund_code="008114",
            fund_name="B",
            holding_amount=1000,
            sector_name="半导体",
        ),
    ]
    assert sector_labels_from_holdings(holdings) == ["半导体"]


def test_signal_backtest_for_sector_lookup():
    context = {
        "has_data": True,
        "sectors": [
            {
                "sector_label": "半导体",
                "by_rule": {
                    "reversal_down": {"hit_rate_percent": 55.0, "trigger_count": 10}
                },
            }
        ],
    }
    entry = signal_backtest_for_sector("半导体", context)
    assert entry is not None
    assert entry["by_rule"]["reversal_down"]["hit_rate_percent"] == 55.0
