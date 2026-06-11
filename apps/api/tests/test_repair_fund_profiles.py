import json

from app.config import refresh_settings
from app.database import _connect
from app.models import FundProfile
from tests.conftest import auth_client_for_db


def test_repair_fund_profile_sectors_clears_plus_label(tmp_path, monkeypatch):
    refresh_settings()
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")
    user_id = client.get("/api/auth/me").json()["id"]

    dirty = FundProfile(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        aliases=["银河创新成长混合A"],
        holding_amount=4042.24,
        sector_name="+",
        sector_return_percent=4.01,
        source="yangjibao-detail",
        is_provisional=False,
    )
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO fund_profiles (userId, fund_code, fund_name, payload, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                user_id,
                dirty.fund_code,
                dirty.fund_name,
                json.dumps(dirty.model_dump(mode="json"), ensure_ascii=False),
            ),
        )
        connection.commit()

    response = client.post("/api/fund-profiles/repair-sectors")
    assert response.status_code == 200
    assert response.json()["repaired"] == 1

    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM fund_profiles WHERE userId = ? AND fund_code = ?",
            (user_id, "519674"),
        ).fetchone()
    payload = json.loads(row["payload"])
    assert payload["sector_name"] is None
