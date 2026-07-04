from __future__ import annotations

"""板块信号回测的共享统计口径（自然基准 + edge + 显著性）。

抽取自 `sector_signal_backtest.py`（模块3-3B "Bug B" 修复引入的方向感知自然基线逻辑），
2026-07 量价背离回测（M1.3）新增 `sector_flow_divergence_backtest.py` 时抽出，避免两套
"该信号算不算有效"的判定标准各写一份、后续维护各自漂移。

`sector_signal_backtest.py` 保留原有的 `MIN_TRIGGERS_FOR_SIGNIFICANCE` /
`EDGE_MIN_PERCENT` / `_direction_fractions` / `_baseline_prob` / `_finalize_bucket`
作为该常量的重导出别名，向后兼容任何既有引用（当前项目内无外部引用，纯防御）。
"""

from typing import Any

# 触发次数太少时不下「有效」结论（可能是运气）。
MIN_TRIGGERS_FOR_SIGNIFICANCE = 30
# 命中率需超过自然基准至少这么多个百分点才算真有超额。
EDGE_MIN_PERCENT = 5.0
# 涨跌幅绝对值小于该阈值视为「平」（与 sector_signal_rules.FLAT_THRESHOLD 对齐）。
FLAT_THRESHOLD = 0.3


def direction_fractions(changes: list[float]) -> tuple[float, float, float]:
    """一组日涨跌的方向分布（up, down, flat 占比）。空集返回全 0。"""
    if not changes:
        return 0.0, 0.0, 0.0
    up = down = flat = 0
    for ch in changes:
        if abs(ch) < FLAT_THRESHOLD:
            flat += 1
        elif ch > 0:
            up += 1
        else:
            down += 1
    total = len(changes)
    return up / total, down / total, flat / total


def baseline_prob(prediction: str, fracs: tuple[float, float, float]) -> float:
    """某预测方向在随机时点上「自然命中」的概率（方向感知的 base rate）。"""
    up, down, flat = fracs
    if prediction == "up":
        return up
    if prediction == "down":
        return down
    if prediction == "down_or_flat":
        return down + flat
    return 0.0


def new_bucket(rule_id: str, label: str) -> dict[str, Any]:
    """初始化一个空的回测统计桶（配合 record_trigger / finalize_bucket 累积使用）。"""
    return {
        "rule_id": rule_id,
        "label": label,
        "trigger_count": 0,
        "hit_count": 0,
        "miss_count": 0,
        "expected_random_hits": 0.0,
        "hit_rate_percent": None,
    }


def record_trigger(
    bucket: dict[str, Any],
    *,
    prediction: str,
    fracs: tuple[float, float, float],
    hit: bool,
) -> None:
    """记录一次规则触发的结果（命中/未命中），累积基准期望命中数供 finalize_bucket 使用。"""
    bucket["trigger_count"] += 1
    bucket["expected_random_hits"] += baseline_prob(prediction, fracs)
    if hit:
        bucket["hit_count"] += 1
    else:
        bucket["miss_count"] += 1


def finalize_bucket(bucket: dict[str, Any]) -> None:
    """就地补全命中率/基准/超额/显著性。

    显著 = 触发次数 >= 门槛 且 命中率超过基准 > EDGE_MIN_PERCENT。
    `beats_random` 保留为 `beats_baseline` 的向后兼容别名。
    """
    triggers = int(bucket.get("trigger_count") or 0)
    if triggers <= 0:
        bucket["hit_rate_percent"] = None
        bucket["baseline_rate_percent"] = None
        bucket["edge_percent"] = None
        bucket["significant"] = False
        bucket["beats_baseline"] = False
        bucket["beats_random"] = False
        return
    hit_rate = bucket["hit_count"] / triggers * 100
    baseline = float(bucket.get("expected_random_hits") or 0.0) / triggers * 100
    edge = hit_rate - baseline
    significant = triggers >= MIN_TRIGGERS_FOR_SIGNIFICANCE and edge > EDGE_MIN_PERCENT
    bucket["hit_rate_percent"] = round(hit_rate, 1)
    bucket["baseline_rate_percent"] = round(baseline, 1)
    bucket["edge_percent"] = round(edge, 1)
    bucket["significant"] = significant
    bucket["beats_baseline"] = significant
    bucket["beats_random"] = significant
