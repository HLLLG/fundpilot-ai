#!/usr/bin/env python3
"""【GATE】美股概览数据源可行性 spike.

仿 ``diagnose_sector_quotes.py``，经 AkShare 子进程（清代理 + JSON stdout 约定）
分别实跑美股指数期货与 USD/CNY 外汇候选接口，对每个源断言：

    - 返回 DataFrame 非空
    - 含目标品种行（纳指 / 标普 / 道指 期货；USD/CNY）
    - 数值列可转 ``float``

并将每个源的真实返回落盘为离线 fixture（JSON）到 ``apps/api/tests/fixtures/``，
供后续 pytest stub 复用；最后输出**可行性结论 / 降级矩阵**。

严禁回退到指数收盘价或占位常量：任一主选源不可用即标注备选或将对应数据项默认
``unavailable``。

GATE 备注（akshare==1.18.64 实跑修正）::

    设计文档拟用的 ``futures_global_em()`` 在 akshare 1.18.64 **不存在**
    （``hasattr(ak, "futures_global_em") is False``）。本环境真实期货实时源为
    ``futures_global_spot_em()``，其中美股指数期货以 CME E-mini 命名出现：
    「小型纳指…」/「小型标普…」/「小型道指…」，并以「当月连续」为代表性主力合约。
    后续 ``us_futures_client.py`` 应改用 ``futures_global_spot_em()`` 并按
    「小型纳指/标普/道指 + 当月连续」匹配，**不得**使用指数/收盘接口作为数值来源。

用法::

    python apps/api/scripts/diagnose_us_market.py            # 紧凑 JSON
    python apps/api/scripts/diagnose_us_market.py --pretty   # 美化输出
    python apps/api/scripts/diagnose_us_market.py --no-write-fixtures
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = API_ROOT / "tests" / "fixtures"

_SUBPROCESS_TIMEOUT = 90

# ---------------------------------------------------------------------------
# 子进程：在独立解释器中清代理后调用 AkShare，结果以 JSON 打到 stdout。
# 复用 akshare_subprocess.py / akshare_spot_client.py 的约定。
# ---------------------------------------------------------------------------

_CHILD_PREAMBLE = """
import json, os, sys
# 清除所有代理 / CA 环境变量，确保子进程直连
for key in list(os.environ):
    if "proxy" in key.lower() or "http" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"
os.environ.pop("REQUESTS_CA_BUNDLE", None)
os.environ.pop("CURL_CA_BUNDLE", None)


def _emit_frame(frame):
    if frame is None or getattr(frame, "empty", True):
        print(json.dumps({"error": "empty"}, ensure_ascii=False))
        return
    columns = [str(c) for c in frame.columns]
    # to_json 会把 numpy 类型与 NaN(->null) 处理为 JSON 安全值，忠实保留原始返回。
    records = json.loads(frame.to_json(orient="records", force_ascii=False))
    print(json.dumps({"columns": columns, "records": records}, ensure_ascii=False))
"""

_FUTURES_SCRIPT = _CHILD_PREAMBLE + """
try:
    import akshare as ak
    # GATE 修正：futures_global_em 在 akshare 1.18.64 不存在；
    # futures_global_spot_em 为本环境真实期货实时源（含 CME E-mini 美股指数期货）。
    if not hasattr(ak, "futures_global_spot_em"):
        print(json.dumps({"error": "futures_global_spot_em missing in this akshare"}, ensure_ascii=False))
        sys.exit(1)
    frame = ak.futures_global_spot_em()
    _emit_frame(frame)
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""

_FX_BAIDU_SCRIPT = _CHILD_PREAMBLE + """
try:
    import akshare as ak
    frame = ak.fx_quote_baidu(symbol="美元")
    _emit_frame(frame)
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""

_BOC_SINA_SCRIPT = _CHILD_PREAMBLE + """
try:
    import akshare as ak
    frame = ak.currency_boc_sina(symbol="美元")
    _emit_frame(frame)
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""


def _run_akshare(script: str) -> tuple[dict[str, Any] | None, str | None]:
    """运行子进程，返回 (payload, error)。payload 为 {"columns","records"}。"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"subprocess timeout after {_SUBPROCESS_TIMEOUT}s"
    except OSError as exc:
        return None, f"subprocess OSError: {exc}"

    if not completed.stdout.strip():
        stderr = (completed.stderr or "").strip()
        return None, f"no stdout (rc={completed.returncode}); stderr={stderr[:300]}"

    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed: {exc}; stdout={completed.stdout[:300]}"

    if isinstance(payload, dict) and payload.get("error"):
        return None, str(payload["error"])
    if not isinstance(payload, dict) or "records" not in payload:
        return None, f"unexpected payload shape: {str(payload)[:200]}"
    return payload, None


# ---------------------------------------------------------------------------
# 分析辅助
# ---------------------------------------------------------------------------


def _is_floatable(value: Any) -> bool:
    if value is None:
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _row_text(record: dict[str, Any]) -> str:
    return " ".join(str(v) for v in record.values() if v is not None)


# futures_global_spot_em 的名称列候选与数值列候选
_FUTURES_NAME_COLS = ("名称", "name")
_FUTURES_NUMERIC_COLS = ("最新价", "涨跌幅")

# 期货品种匹配关键字（任一命中即算匹配）。akshare 1.18.64 的 futures_global_spot_em
# 以 CME E-mini 命名美股指数期货：小型纳指 / 小型标普 / 小型道指。
_FUTURES_TARGETS: dict[str, tuple[str, ...]] = {
    "NASDAQ_FUT": ("小型纳指", "纳斯达克", "纳指"),
    "SP500_FUT": ("小型标普", "标普500", "标普"),
    "DOW_FUT": ("小型道指", "道琼斯", "道指"),
}

# 同一品种存在多个到期月合约时，优先选取「当月连续」主力合约。
_FRONT_MONTH_HINTS = ("当月连续", "连续")


def _pick_name(record: dict[str, Any]) -> str:
    for col in _FUTURES_NAME_COLS:
        if col in record and record[col] is not None:
            return str(record[col])
    # 退化：取首个字符串列
    for value in record.values():
        if isinstance(value, str):
            return value
    return ""


def _analyze_futures(payload: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = payload.get("records") or []
    columns: list[str] = payload.get("columns") or []
    numeric_cols = [c for c in _FUTURES_NUMERIC_COLS if c in columns]

    # 先按 symbol 收集全部候选行，再优先挑「当月连续」主力合约。
    candidates: dict[str, list[dict[str, Any]]] = {s: [] for s in _FUTURES_TARGETS}
    for record in records:
        name = _pick_name(record)
        if not name:
            continue
        for symbol, keywords in _FUTURES_TARGETS.items():
            if any(k in name for k in keywords):
                candidates[symbol].append({"name": name, "record": record})
                break

    matched: dict[str, dict[str, Any]] = {}
    numeric_ok: dict[str, bool] = {}
    for symbol, cands in candidates.items():
        if not cands:
            continue
        chosen = _pick_front_month(cands)
        record = chosen["record"]
        matched[symbol] = {
            "matched_name": chosen["name"],
            "last_price": record.get("最新价"),
            "change_percent": record.get("涨跌幅"),
        }
        numeric_ok[symbol] = (
            all(_is_floatable(record.get(col)) for col in numeric_cols)
            if numeric_cols
            else False
        )

    missing = [s for s in _FUTURES_TARGETS if s not in matched]
    all_numeric = bool(matched) and all(numeric_ok.get(s, False) for s in matched)
    ok = bool(records) and not missing and all_numeric

    return {
        "name": "futures_global_spot_em",
        "purpose": "美股指数期货（小型纳指/标普/道指 当月连续）",
        "priority": "primary",
        "note": "设计文档的 futures_global_em 在 akshare 1.18.64 不存在；本源为实跑确认的真实替代。",
        "ok": ok,
        "row_count": len(records),
        "columns": columns,
        "numeric_columns_checked": numeric_cols,
        "matched": matched,
        "missing_symbols": missing,
        "numeric_floatable": numeric_ok,
        "error": None if ok else _futures_error(records, missing, all_numeric),
    }


def _pick_front_month(cands: list[dict[str, Any]]) -> dict[str, Any]:
    for hint in _FRONT_MONTH_HINTS:
        for cand in cands:
            if hint in cand["name"]:
                return cand
    return cands[0]


def _futures_error(records: list, missing: list[str], all_numeric: bool) -> str:
    if not records:
        return "empty DataFrame"
    if missing:
        return f"missing target rows: {missing}"
    if not all_numeric:
        return "numeric columns (最新价/涨跌幅) not float-convertible"
    return "unknown"


def _analyze_forex(
    payload: dict[str, Any],
    *,
    source_name: str,
    priority: str,
    purpose: str,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = payload.get("records") or []
    columns: list[str] = payload.get("columns") or []

    # 目标品种行：包含「美元」或 USD/CNY 字样
    target_rows = [
        r for r in records
        if any(tok in _row_text(r) for tok in ("美元", "USD", "usd"))
    ]
    # 若整张表本身即为「美元」报价（如 fx_quote_baidu），则所有行均视为目标
    if not target_rows and records:
        target_rows = records

    # 至少一个数值列在目标行可转 float
    numeric_hits: list[dict[str, Any]] = []
    for record in target_rows:
        floatable_cols = {
            col: record.get(col)
            for col in columns
            if _is_floatable(record.get(col))
        }
        if floatable_cols:
            numeric_hits.append(
                {"row": _row_text(record)[:80], "floatable": floatable_cols}
            )

    ok = bool(records) and bool(target_rows) and bool(numeric_hits)

    error = None
    if not records:
        error = "empty DataFrame"
    elif not target_rows:
        error = "no USD/CNY target row"
    elif not numeric_hits:
        error = "no float-convertible numeric column on target row"

    return {
        "name": source_name,
        "purpose": purpose,
        "priority": priority,
        "ok": ok,
        "row_count": len(records),
        "columns": columns,
        "target_row_count": len(target_rows),
        "numeric_sample": numeric_hits[:3],
        "error": error,
    }


# ---------------------------------------------------------------------------
# fixtures 落盘
# ---------------------------------------------------------------------------


def _write_fixture(filename: str, payload: dict[str, Any]) -> str | None:
    try:
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        path = FIXTURES_DIR / filename
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path.relative_to(API_ROOT))
    except OSError as exc:
        return f"<write failed: {exc}>"


# ---------------------------------------------------------------------------
# 降级矩阵 / 可行性结论
# ---------------------------------------------------------------------------


def _build_degradation_matrix(
    futures: dict[str, Any],
    fx_baidu: dict[str, Any],
    boc_sina: dict[str, Any],
) -> dict[str, Any]:
    # 期货
    if futures["ok"]:
        futures_decision = "ok: 使用 futures_global_spot_em 真实期货（小型纳指/标普/道指 当月连续）"
        futures_status = "ok"
    else:
        futures_decision = (
            "unavailable: 主选不可用且本任务无备选实跑 → 期货数据项默认 unavailable，"
            "严禁回退指数收盘价/占位常量"
        )
        futures_status = "unavailable"

    # 外汇：主选 baidu → 备选 boc_sina → unavailable
    if fx_baidu["ok"]:
        forex_decision = "ok: 使用 fx_quote_baidu(美元) 实时 USD/CNY"
        forex_status = "ok"
        forex_chosen = "fx_quote_baidu"
    elif boc_sina["ok"]:
        forex_decision = (
            "fallback: fx_quote_baidu 不可用 → 改用 currency_boc_sina(美元) 中行牌价"
            "（仍为真实汇率，标注日频时效偏差）"
        )
        forex_status = "ok"
        forex_chosen = "currency_boc_sina"
    else:
        forex_decision = (
            "unavailable: 主选与备选均不可用 → USD/CNY 默认 unavailable，禁止填占位常量"
        )
        forex_status = "unavailable"
        forex_chosen = None

    return {
        "futures": {"status": futures_status, "decision": futures_decision},
        "forex": {
            "status": forex_status,
            "chosen_source": forex_chosen,
            "decision": forex_decision,
        },
        "hard_rule": "任一主选源不可用即标注备选或对应数据项默认 unavailable；严禁回退指数收盘价或占位常量。",
    }


def _conclusion(matrix: dict[str, Any]) -> str:
    f_ok = matrix["futures"]["status"] == "ok"
    x_ok = matrix["forex"]["status"] == "ok"
    if f_ok and x_ok:
        return (
            "FEASIBLE: 期货与 USD/CNY 真实数据源在本环境均可达，可继续后续任务。"
        )
    if f_ok and not x_ok:
        return (
            "PARTIAL: 期货可达但 USD/CNY 不可达 → 汇率数据项按 unavailable 实现降级，"
            "其余任务可继续。"
        )
    if not f_ok and x_ok:
        return (
            "PARTIAL: USD/CNY 可达但期货不可达 → 期货数据项按 unavailable 实现降级，"
            "QDII 参考涨跌随之置 None；其余任务可继续。"
        )
    return (
        "BLOCKED-DEGRADED: 期货与外汇主/备选源在本环境均不可达 → 两数据项均按 "
        "unavailable 实现，特性仍须优雅降级（绝不编造数值）。可继续实现但 UI 将主要"
        "展示不可用态，建议后续在可联网环境复跑本 spike。"
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_diagnostic(*, write_fixtures: bool = True) -> dict[str, Any]:
    started = time.time()
    sources: list[dict[str, Any]] = []
    fixtures_written: dict[str, str | None] = {}

    # 1) 期货
    fut_payload, fut_err = _run_akshare(_FUTURES_SCRIPT)
    if fut_payload is not None:
        futures = _analyze_futures(fut_payload)
        if write_fixtures and fut_payload.get("records"):
            fixtures_written["futures_global_spot_em"] = _write_fixture(
                "us_futures_global_spot_em.json", fut_payload
            )
    else:
        futures = {
            "name": "futures_global_em",
            "purpose": "美股指数期货（纳指/标普/道指期货）",
            "priority": "primary",
            "ok": False,
            "row_count": 0,
            "error": fut_err,
        }
    sources.append(futures)

    # 2) 外汇主选 fx_quote_baidu
    fx_payload, fx_err = _run_akshare(_FX_BAIDU_SCRIPT)
    if fx_payload is not None:
        fx_baidu = _analyze_forex(
            fx_payload,
            source_name="fx_quote_baidu",
            priority="primary",
            purpose="USD/CNY 实时外汇",
        )
        if write_fixtures and fx_payload.get("records"):
            fixtures_written["fx_quote_baidu"] = _write_fixture(
                "us_fx_quote_baidu.json", fx_payload
            )
    else:
        fx_baidu = {
            "name": "fx_quote_baidu",
            "purpose": "USD/CNY 实时外汇",
            "priority": "primary",
            "ok": False,
            "row_count": 0,
            "error": fx_err,
        }
    sources.append(fx_baidu)

    # 3) 外汇备选 currency_boc_sina
    boc_payload, boc_err = _run_akshare(_BOC_SINA_SCRIPT)
    if boc_payload is not None:
        boc_sina = _analyze_forex(
            boc_payload,
            source_name="currency_boc_sina",
            priority="fallback",
            purpose="USD/CNY 中行牌价（日频兜底）",
        )
        if write_fixtures and boc_payload.get("records"):
            fixtures_written["currency_boc_sina"] = _write_fixture(
                "us_currency_boc_sina.json", boc_payload
            )
    else:
        boc_sina = {
            "name": "currency_boc_sina",
            "purpose": "USD/CNY 中行牌价（日频兜底）",
            "priority": "fallback",
            "ok": False,
            "row_count": 0,
            "error": boc_err,
        }
    sources.append(boc_sina)

    matrix = _build_degradation_matrix(futures, fx_baidu, boc_sina)
    conclusion = _conclusion(matrix)

    return {
        "ok": matrix["futures"]["status"] == "ok" and matrix["forex"]["status"] == "ok",
        "elapsed_ms": int(round((time.time() - started) * 1000)),
        "sources": sources,
        "fixtures_written": fixtures_written,
        "degradation_matrix": matrix,
        "feasibility_conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose US market data source feasibility")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument(
        "--no-write-fixtures",
        action="store_true",
        help="Do not persist offline fixtures",
    )
    args = parser.parse_args()

    result = run_diagnostic(write_fixtures=not args.no_write_fixtures)
    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    # 期货与外汇任一可达即视为可继续（PARTIAL 仍允许后续降级实现）。
    forex_ok = result["degradation_matrix"]["forex"]["status"] == "ok"
    futures_ok = result["degradation_matrix"]["futures"]["status"] == "ok"
    return 0 if (forex_ok or futures_ok) else 2


if __name__ == "__main__":
    raise SystemExit(main())
