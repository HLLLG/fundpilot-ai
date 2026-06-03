"""在独立子进程调用 AkShare，避免 py_mini_racer 在主进程中 crash."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from functools import lru_cache

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 60


@lru_cache(maxsize=128)
def fetch_fund_nav_history(fund_code: str, trading_days: int = 90) -> dict | None:
    """在子进程中获取基金净值走势，避免 py_mini_racer crash 主进程."""
    script = f"""
import akshare as ak
import json
try:
    frame = ak.fund_open_fund_info_em(symbol="{fund_code}", indicator="单位净值走势")
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
        # 保留最后 {trading_days} 条记录
        if len(frame) > {trading_days}:
            frame = frame.iloc[-{trading_days}:]
        data = []
        for _, row in frame.iterrows():
            data.append({{
                "date": str(row.get("净值日期", "")),
                "nav": float(row.get("单位净值", 0)) if row.get("单位净值") else None,
                "daily_growth": float(row.get("日增长率", 0)) if row.get("日增长率") else None,
            }})
        print(json.dumps({{"data": data}}))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning(f"akshare subprocess failed for {{fund_code}}: stderr={{result.stderr}}")
            return None

        output = json.loads(result.stdout.strip())
        if "error" in output:
            logger.debug(f"akshare returned error for {{fund_code}}: {{output['error']}}")
            return None

        return output
    except subprocess.TimeoutExpired:
        logger.warning(f"akshare subprocess timeout for {{fund_code}}")
        return None
    except Exception as e:
        logger.error(f"akshare subprocess exception for {{fund_code}}: {{e}}")
        return None


def fetch_sector_boards_via_akshare(include_index: bool = True) -> dict:
    """在子进程中获取板块行情，避免 py_mini_racer crash."""
    script = f"""
import akshare as ak
import json
try:
    concept_df = ak.stock_board_concept_name_em()
    industry_df = ak.stock_board_industry_name_em()

    concept = {{}};
    industry = {{}};
    index = {{}};

    if concept_df is not None and not concept_df.empty:
        for _, row in concept_df.iterrows():
            name = row.get("板块名称")
            change = row.get("涨跌幅")
            if name and change is not None:
                try:
                    concept[str(name)] = float(change) / 100
                except:
                    pass

    if industry_df is not None and not industry_df.empty:
        for _, row in industry_df.iterrows():
            name = row.get("行业名称")
            change = row.get("涨跌幅")
            if name and change is not None:
                try:
                    industry[str(name)] = float(change) / 100
                except:
                    pass

    print(json.dumps({{"concept": concept, "industry": industry, "index": index}}))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning(f"akshare boards subprocess failed: {{result.stderr}}")
            return {"concept": {}, "industry": {}, "index": {}}

        output = json.loads(result.stdout.strip())
        if "error" in output:
            logger.debug(f"akshare boards returned error: {{output['error']}}")
            return {"concept": {}, "industry": {}, "index": {}}

        return output
    except subprocess.TimeoutExpired:
        logger.warning("akshare boards subprocess timeout")
        return {"concept": {}, "industry": {}, "index": {}}
    except Exception as e:
        logger.error(f"akshare boards subprocess exception: {{e}}")
        return {"concept": {}, "industry": {}, "index": {}}


def clear_nav_cache() -> None:
    """清空缓存，用于测试或需要刷新数据时."""
    fetch_fund_nav_history.cache_clear()
