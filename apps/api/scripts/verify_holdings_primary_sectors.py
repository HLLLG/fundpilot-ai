#!/usr/bin/env python3
"""核对当前用户持仓的关联板块：主动基金看季报穿透证据，被动基金看合同指数。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def main() -> int:
    from app.database import get_fund_primary_sectors_by_codes, list_distinct_portfolio_user_ids
    from app.request_context import reset_request_user_id, set_request_user_id
    from app.services.fund_holdings_sector_infer import (
        assess_sector_from_portfolio_stocks,
        fetch_portfolio_stocks_with_industry_evidence,
    )
    from app.services.fund_primary_sector_service import _is_passive_index_fund_name
    from app.services.portfolio_holdings_service import load_persisted_holdings

    seen: set[str] = set()
    for user_id in list_distinct_portfolio_user_ids():
        token = set_request_user_id(user_id)
        try:
            holdings, *_ = load_persisted_holdings(fetch_benchmark=False)
            codes = [
                holding.fund_code
                for holding in holdings
                if holding.fund_code and holding.fund_code != "000000"
            ]
            rows = get_fund_primary_sectors_by_codes(codes) if codes else {}
            for holding in holdings:
                code = holding.fund_code
                if not code or code == "000000" or code in seen:
                    continue
                seen.add(code)
                row = rows.get(code) or {}
                passive = _is_passive_index_fund_name(holding.fund_name)
                payload = {
                    "fund_code": code,
                    "fund_name": holding.fund_name,
                    "stored_sector": holding.sector_name,
                    "stored_index": holding.intraday_index_name,
                    "stored_source": row.get("source"),
                    "passive": passive,
                }
                if passive:
                    payload["verdict"] = "ok_passive_tracking"
                    payload["reason"] = "被动指数/联接，关联板块应跟合同跟踪指数，不按重仓行业改写"
                    print(json.dumps(payload, ensure_ascii=False))
                    continue
                evidence = fetch_portfolio_stocks_with_industry_evidence(code)
                stocks = list(evidence.get("stocks") or [])
                assessment = assess_sector_from_portfolio_stocks(stocks)
                top = []
                for stock in stocks[:8]:
                    top.append(
                        {
                            "name": stock.name,
                            "code": stock.stock_code,
                            "weight": stock.weight,
                            "industry": stock.industry,
                            "theme": stock.theme,
                        }
                    )
                inferred = assessment.get("sector_name")
                qualification = assessment.get("qualification") or {}
                scores = assessment.get("scores") or {}
                payload.update(
                    {
                        "inferred_sector": inferred,
                        "scores": scores,
                        "qualification": qualification,
                        "top_holdings": top,
                        "report_period": evidence.get("report_period"),
                    }
                )
                if inferred and inferred == holding.sector_name:
                    payload["verdict"] = "ok_holdings_match"
                    payload["reason"] = "季报穿透主板块与已落库标签一致"
                elif inferred and inferred != holding.sector_name:
                    payload["verdict"] = "mismatch"
                    payload["reason"] = "季报穿透主板块与已落库标签不一致"
                elif qualification.get("sector_inference_eligible") is not True:
                    payload["verdict"] = "ok_ineligible_keep"
                    payload["reason"] = "穿透证据不够格，未强行改板块"
                else:
                    payload["verdict"] = "review"
                    payload["reason"] = "需要人工看证据"
                print(json.dumps(payload, ensure_ascii=False))
        finally:
            reset_request_user_id(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
