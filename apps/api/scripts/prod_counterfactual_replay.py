#!/usr/bin/env python3
"""Read-only: 线上 decision_events × outcome_observations 的费后对照摘要。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from app.config import get_settings
from app.db_connect import _parse_mysql_url
from app.services.outcome_path_metrics import evaluate_no_action_counterfactual


def _obj(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _dig(row: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        cursor: Any = row
        ok = True
        for key in path:
            if not isinstance(cursor, dict) or key not in cursor:
                ok = False
                break
            cursor = cursor[key]
        if ok:
            return cursor
    return None


def main() -> None:
    settings = get_settings()
    conn = pymysql.connect(
        **(_parse_mysql_url(settings.database_url or "") | {"cursorclass": DictCursor})
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT userId, event_id, source_type, source_report_id, decision_date, "
                "fund_code, fund_name, final_action, action_category, eligible, "
                "amount_yuan, metric_eligible, payload FROM decision_events"
            )
            events = list(cur.fetchall())
            cur.execute(
                "SELECT userId, observation_id, decision_event_id, horizon_trading_days, "
                "target_date, status, payload FROM outcome_observations"
            )
            observations = list(cur.fetchall())
    finally:
        conn.close()

    events_by_id = {(int(row["userId"]), str(row["event_id"])): row for row in events}
    print("=== 库概况 ===")
    print(
        f"decision_events={len(events)}  outcome_observations={len(observations)}  "
        f"users={sorted({int(r['userId']) for r in events})}"
    )
    print("events_by_source", dict(Counter(str(r["source_type"]) for r in events)))
    print(
        "daily_actions",
        dict(
            Counter(
                str(r["final_action"])
                for r in events
                if r["source_type"] == "daily"
            )
        ),
    )
    print(
        "daily_classes",
        dict(
            Counter(
                str(r["action_category"])
                for r in events
                if r["source_type"] == "daily"
            )
        ),
    )

    sample_event = next((r for r in events if r["source_type"] == "daily"), None)
    sample_obs = next((r for r in observations if r["status"] in {"hit", "miss", "mature"}), None)
    if sample_event:
        payload = _obj(sample_event["payload"])
        print("event_payload_keys", sorted(payload.keys())[:40])
        rec = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else {}
        print(
            "event_rec_keys",
            sorted(rec.keys())[:30] if rec else [],
            "evaluation_class",
            payload.get("evaluation_class"),
            "suggested_pct",
            rec.get("suggested_position_change_percent") if rec else None,
        )
    if sample_obs:
        payload = _obj(sample_obs["payload"])
        print("obs_payload_keys", sorted(payload.keys())[:50])
        cf = payload.get("no_action_counterfactual")
        print("counterfactual_sample", cf if isinstance(cf, dict) else type(cf).__name__)
        metrics = payload.get("metrics")
        print("metrics_type", type(metrics).__name__, list(metrics)[:8] if isinstance(metrics, dict) else None)

    def _infer_percent(action: str, klass: str, frozen: float | None) -> float | None:
        if frozen not in (None, 0):
            return frozen
        if klass == "bearish" or any(token in action for token in ("减仓", "清仓", "风控", "复核")):
            if "清仓" in action:
                return -100.0
            if "大幅" in action:
                return -50.0
            if "减仓" in action or "风控" in action or "复核" in action:
                return -25.0
        return None

    def _summarize(title: str, subset: list[dict[str, Any]], value_key: str) -> None:
        values = [float(row[value_key]) for row in subset if row.get(value_key) is not None]
        if not values:
            print(f"{title}: 0 条")
            return
        hits = sum(1 for value in values if value > 0)
        print(
            f"{title}: n={len(values)}  均值 {mean(values):+.3f}%  "
            f"中位 {sorted(values)[len(values)//2]:+.3f}%  "
            f"听建议更好 {hits}/{len(values)}={hits/len(values)*100:.1f}%"
        )

    rows: list[dict[str, Any]] = []
    for obs in observations:
        event = events_by_id.get((int(obs["userId"]), str(obs["decision_event_id"])))
        if event is None:
            continue
        event_payload = _obj(event["payload"])
        obs_payload = _obj(obs["payload"])
        rec = event_payload.get("recommendation")
        if not isinstance(rec, dict):
            rec = {}
        stored_cf = obs_payload.get("no_action_counterfactual")
        if not isinstance(stored_cf, dict):
            stored_cf = {}
        metrics = obs_payload.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        net = _dig(
            metrics,
            ("positive_net_return", "value_percent"),
            ("net_return", "value_percent"),
        )
        gross = _dig(
            metrics,
            ("gross_direction", "value_percent"),
            ("gross_return", "value_percent"),
        )
        if gross is None:
            gross = _num(obs_payload.get("gross_total_return_percent"))
        klass = str(
            event_payload.get("evaluation_class")
            or event["action_category"]
            or ""
        )
        action = str(event["final_action"] or "")
        frozen_pct = _num(rec.get("suggested_position_change_percent"))
        inferred_pct = _infer_percent(action, klass, frozen_pct)
        fee_policy = event_payload.get("fee_policy") or obs_payload.get("fee_policy")
        formal = evaluate_no_action_counterfactual(
            gross_return_percent=_num(gross),
            evaluation_class=klass,
            recommendation={"suggested_position_change_percent": frozen_pct},
            fee_policy=fee_policy,
        )
        informal = evaluate_no_action_counterfactual(
            gross_return_percent=_num(gross),
            evaluation_class=klass,
            recommendation={"suggested_position_change_percent": inferred_pct},
            fee_policy=fee_policy,
        )
        rows.append(
            {
                "user": int(event["userId"]),
                "source": str(event["source_type"]),
                "date": str(event["decision_date"]),
                "code": str(event["fund_code"] or ""),
                "name": str(event["fund_name"] or ""),
                "action": action,
                "klass": klass,
                "eligible": int(event["eligible"] or 0),
                "metric_eligible": int(event["metric_eligible"] or 0),
                "horizon": int(obs["horizon_trading_days"]),
                "status": str(obs["status"]),
                "target": str(obs["target_date"] or ""),
                "pct": frozen_pct,
                "inferred_pct": inferred_pct,
                "cf_available": bool(formal.get("available")),
                "cf_reason": formal.get("unavailable_reason"),
                "cf_value": _num(formal.get("incremental_value_add_percent")),
                "cf_hit": formal.get("hit"),
                "informal_available": bool(informal.get("available")),
                "informal_value": _num(informal.get("incremental_value_add_percent")),
                "amount": _num(event.get("amount_yuan") or rec.get("amount_yuan")),
                "gross": _num(gross),
                "net": _num(net),
                "direction_hit": _dig(metrics, ("gross_direction", "hit")),
                "net_hit": _dig(metrics, ("positive_net_return", "hit")),
            }
        )

    print("\n=== 反事实可算性 ===")
    for source in ("daily", "discovery"):
        subset = [r for r in rows if r["source"] == source]
        print(
            source,
            "obs",
            len(subset),
            "cf_available",
            sum(1 for r in subset if r["cf_available"]),
            "reasons",
            dict(Counter(str(r["cf_reason"] or "available" if r["cf_available"] else r["cf_reason"]) for r in subset)),
        )

    print("\n=== 按用户/来源的事件日 ===")
    for source in ("daily", "discovery"):
        for user in (1, 2):
            dates = sorted(
                {
                    str(event["decision_date"])
                    for event in events
                    if event["source_type"] == source and int(event["userId"]) == user
                }
            )
            if dates:
                print(f"u{user} {source}: {len(dates)} 天  {dates[0]} → {dates[-1]}")

    print("\n=== 正式口径：冻住比例 × 费后反事实 ===")
    for source in ("daily", "discovery"):
        for horizon in (5, 20, 60):
            subset = [
                r
                for r in rows
                if r["source"] == source
                and r["horizon"] == horizon
                and r["status"] in {"hit", "miss", "mature"}
                and r["cf_available"]
            ]
            _summarize(f"{source} T+{horizon} 正式", subset, "cf_value")

    print("\n=== 非正式：缺比例的减仓按动作补 -25/-50，只用已成熟毛收益 ===")
    for source in ("daily",):
        for horizon in (5, 20, 60):
            subset = [
                r
                for r in rows
                if r["source"] == source
                and r["horizon"] == horizon
                and r["status"] in {"hit", "miss", "mature"}
                and r["informal_available"]
            ]
            _summarize(f"{source} T+{horizon} 非正式", subset, "informal_value")
            by_action: dict[str, list[float]] = defaultdict(list)
            for row in subset:
                if row["informal_value"] is not None:
                    by_action[row["action"]].append(float(row["informal_value"]))
            for action, nums in sorted(by_action.items()):
                print(
                    f"    {action}: n={len(nums)} 均值 {mean(nums):+.3f}%  "
                    f"更好 {sum(1 for x in nums if x > 0)}/{len(nums)}"
                )

    print("\n=== 成熟方向命中（status in hit/miss/mature，有仓位变动） ===")
    for source in ("daily", "discovery"):
        for horizon in (5, 20, 60):
            subset = [
                r
                for r in rows
                if r["source"] == source
                and r["horizon"] == horizon
                and r["status"] in {"hit", "miss", "mature"}
            ]
            if not subset:
                continue
            print(
                f"{source} T+{horizon}: n={len(subset)} "
                f"hit={sum(1 for r in subset if r['status']=='hit')} "
                f"miss={sum(1 for r in subset if r['status']=='miss')} "
                f"mature={sum(1 for r in subset if r['status']=='mature')}"
            )
            actionable = [r for r in subset if r["pct"] not in (None, 0)]
            print(
                f"    带非零比例 {len(actionable)}  "
                f"cf_available {sum(1 for r in actionable if r['cf_available'])}"
            )

    print("\n=== 日报已成熟明细（含非正式增量） ===")
    detail = [
        r
        for r in rows
        if r["source"] == "daily"
        and r["horizon"] in {5, 20}
        and r["status"] in {"hit", "miss", "mature"}
    ]
    detail.sort(key=lambda r: (r["horizon"], r["date"], r["code"]))
    for row in detail:
        print(
            f"u{row['user']} {row['date']} T+{row['horizon']} {row['code']} "
            f"{row['action']} frozen={row['pct']} infer={row['inferred_pct']} "
            f"gross={row['gross']} formal={row['cf_value']} "
            f"informal={row['informal_value']}"
        )

    print("\n=== 荐基 T+5 方向命中（金额在 amount，正式反事实要求比例） ===")
    disc = [
        r
        for r in rows
        if r["source"] == "discovery"
        and r["horizon"] == 5
        and r["status"] in {"hit", "miss", "mature"}
    ]
    print(
        f"n={len(disc)} hit={sum(1 for r in disc if r['status']=='hit')} "
        f"miss={sum(1 for r in disc if r['status']=='miss')} "
        f"direction_hit={sum(1 for r in disc if r['direction_hit'] is True)} "
        f"amount非空={sum(1 for r in disc if r['amount'] not in (None, 0))} "
        f"pct非空={sum(1 for r in disc if r['pct'] not in (None, 0))}"
    )
    for row in disc:
        print(
            f"u{row['user']} {row['date']} {row['code']} {row['action']}/{row['klass']} "
            f"status={row['status']} amt={row['amount']} gross={row['gross']} "
            f"dir_hit={row['direction_hit']} net_hit={row['net_hit']}"
        )

    print("\n=== 日报动作 × 是否有非零比例 ===")
    daily_events = [r for r in events if r["source_type"] == "daily"]
    action_pct = Counter()
    for event in daily_events:
        payload = _obj(event["payload"])
        rec = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else {}
        pct = _num(rec.get("suggested_position_change_percent"))
        key = f"{event['final_action']}|{'nonzero' if pct not in (None, 0) else 'zero/none'}"
        action_pct[key] += 1
    for key, count in sorted(action_pct.items()):
        print(f"  {key}: {count}")


if __name__ == "__main__":
    main()
