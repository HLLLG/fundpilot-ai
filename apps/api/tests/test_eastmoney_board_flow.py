from app.services.eastmoney_spot_client import _parse_board_record_rows


def test_parse_board_record_rows_includes_flow_tiers():
    rows = _parse_board_record_rows(
        [
            {
                "f14": "半导体概念",
                "f12": "BK1036",
                "f3": 2.5,
                "f62": 5829000000.0,
                "f66": 10810000000.0,
                "f72": -4981000000.0,
                "f78": -6184000000.0,
                "f84": 251000000.0,
            }
        ]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "半导体概念"
    assert row["main_force_net_yi"] == 58.29
    assert row["super_large_net_yi"] == 108.1
    assert row["large_net_yi"] == -49.81
    assert row["medium_net_yi"] == -61.84
    assert row["small_net_yi"] == 2.51
