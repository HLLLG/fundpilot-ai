import json
from pathlib import Path

import pytest

from app.models import Holding
from app.services.holding_client import serialize_holding_for_client
from app.services.holding_estimates import compute_daily_profit

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "holding_metrics_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURES, ids=[item["id"] for item in FIXTURES])
def test_holding_client_matches_shared_fixtures(case, monkeypatch):
    monkeypatch.setattr("app.database.get_fund_profile_by_code", lambda code: None)
    holding = Holding.model_validate(case["holding"])
    payload = serialize_holding_for_client(holding)
    expected = case["expected"]

    for key, value in expected.items():
        if key == "daily_profit":
            assert compute_daily_profit(holding) == pytest.approx(value, abs=0.5)
            continue
        if isinstance(value, bool):
            assert payload[key] is value
            continue
        assert payload[key] == pytest.approx(value, abs=0.5)
