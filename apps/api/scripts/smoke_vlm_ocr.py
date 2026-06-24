"""手动 live 验证：对真实截图跑 VLM 识别，打印耗时 + 结构化结果。

用法（需先在 .env 配置 FUND_AI_VLM_OCR_API_KEY）：
    cd apps/api && ./.venv/Scripts/python.exe scripts/smoke_vlm_ocr.py <图片路径> [更多图片...]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from app.services.vlm_holdings_provider import extract_holdings_via_vlm


def main() -> None:
    for path in sys.argv[1:]:
        p = Path(path)
        if not p.is_file():
            print(f"!! 文件不存在: {p}")
            continue
        t0 = time.perf_counter()
        try:
            holdings = extract_holdings_via_vlm(p.read_bytes())
        except Exception as exc:  # noqa: BLE001
            print(f"{p.name}: 失败 {type(exc).__name__}: {exc}")
            continue
        dt = time.perf_counter() - t0
        print(f"{p.name}: {dt:.2f}s, {len(holdings)} 只")
        for h in holdings:
            print(
                f"  - {h.fund_name} | 金额 {h.holding_amount} | "
                f"收益 {h.holding_profit} | {h.holding_return_percent}%"
            )


if __name__ == "__main__":
    main()
