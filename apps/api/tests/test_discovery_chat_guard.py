from app.services.discovery_chat_guard import (
    format_candidate_pool_whitelist,
    sanitize_discovery_chat_fund_codes,
)


def test_format_candidate_pool_whitelist_lists_pool_only():
    report = {
        "candidate_pool": [
            {
                "fund_code": "000845",
                "fund_name": "国投瑞银信息消费混合A",
                "sector_label": "消费电子",
            },
            {
                "fund_code": "006080",
                "fund_name": "海富通电子传媒股票C",
                "sector_label": "消费电子",
            },
        ],
        "recommendations": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长混合A",
                "sector_name": "半导体",
            }
        ],
    }
    text = format_candidate_pool_whitelist(report)
    assert "000845" in text
    assert "006080" in text
    assert "519674" in text
    assert "唯一允许引用" in text


def test_sanitize_replaces_hallucinated_code_with_sector_pool():
    report = {
        "candidate_pool": [
            {
                "fund_code": "000845",
                "fund_name": "国投瑞银信息消费混合A",
                "sector_label": "消费电子",
            },
            {
                "fund_code": "006080",
                "fund_name": "海富通电子传媒股票C",
                "sector_label": "消费电子",
            },
        ],
        "recommendations": [],
    }
    raw = "• 消费电子：159999（消费电子ETF）。"
    cleaned, notes = sanitize_discovery_chat_fund_codes(raw, report)
    assert "159999" not in cleaned
    assert "000845" in cleaned
    assert "006080" in cleaned
    assert notes


def test_sanitize_keeps_allowed_codes():
    report = {
        "candidate_pool": [
            {
                "fund_code": "000845",
                "fund_name": "国投瑞银信息消费混合A",
                "sector_label": "消费电子",
            },
        ],
        "recommendations": [],
    }
    raw = "可关注 000845（国投瑞银信息消费混合A）。"
    cleaned, notes = sanitize_discovery_chat_fund_codes(raw, report)
    assert cleaned == raw
    assert not notes


def test_sanitize_marks_unknown_sector_code():
    report = {
        "candidate_pool": [
            {
                "fund_code": "015945",
                "fund_name": "易方达国防军工混合C",
                "sector_label": "商业航天",
            },
        ],
        "recommendations": [],
    }
    raw = "或军工ETF 512660。"
    cleaned, notes = sanitize_discovery_chat_fund_codes(raw, report)
    assert "512660" not in cleaned
    assert "不在本次候选池" in cleaned
    assert notes
