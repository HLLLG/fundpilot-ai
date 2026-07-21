from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Iterable, Mapping

from app.database import (
    get_fund_primary_sector,
    get_fund_primary_sectors_global_by_codes,
    get_fund_profile_by_code,
    list_fund_primary_sectors,
    save_fund_primary_sector,
)
from app.models import FundProfile, Holding
from app.request_context import try_get_request_user_id
from app.services.fund_primary_sector_global import (
    is_global_sector_fresh,
    load_fresh_global_sector,
    promote_record_to_global,
)
from app.services.fund_profile import (
    _is_valid_sector_label,
    infer_intraday_index_from_fund_name,
)
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_labels import (
    infer_sector_label_from_fund_name,
    infer_semantic_sector_from_fund_name,
    normalize_sector_label,
)

logger = logging.getLogger(__name__)

_BENCHMARK_MISS_TTL = timedelta(hours=24)
_BENCHMARK_MISS_CACHE_MAX_ENTRIES = 512
_benchmark_miss_cache: OrderedDict[str, datetime] = OrderedDict()
_benchmark_miss_cache_lock = RLock()
_GLOBAL_ROW_NOT_PRELOADED = object()

# 已废弃：per-fund 手工种子由业绩基准 / 重仓行业穿透替代（discovery 改读 fund_primary_sectors）。
GLOBAL_FUND_SECTOR_SEEDS: dict[str, dict[str, str | None]] = {}

_SOURCE_PRIORITY = {
    "ocr_detail": 100,
    "manual": 85,
    "holdings_infer": 70,
    "precompute_holdings": 70,
    "benchmark_index": 65,
    "precompute_benchmark": 65,
    "benchmark_freeform": 55,
    "alipay_overview": 50,
    "semantic_name": 40,
    "llm_infer": 30,
    # 基金名称清洗后仍剩一段短语时的"自由主题"猜测，比关键词/白名单命中的 semantic_name
    # 更不可靠（可能只是基金自身营销短语的残留），信任度介于 name_infer 与 llm_infer 之间，
    # 允许持仓穿透/LLM 兜底之后再纠正它。
    "semantic_name_freeform": 25,
    "name_infer": 10,
}

# 仅 OCR 详情 / 手动沉淀的板块可挡住业绩基准；总览推断的 alipay_overview 不可靠。
_HIGH_TRUST_SECTOR_SOURCES = frozenset({"ocr_detail", "manual"})


from app.services.fund_primary_sector_types import PrimarySectorRecord


@dataclass
class PrimarySectorBatchContext:
    """Request-local snapshots used by batch holding resolution.

    The regular single-fund APIs deliberately keep their point-query behavior. Batch
    callers can opt into this context so every resolver in the chain observes the
    same user/global/profile snapshot, including rows written earlier in the batch.
    """

    user_rows_by_code: dict[str, dict] = field(default_factory=dict)
    global_rows_by_code: dict[str, dict] = field(default_factory=dict)
    profiles_by_code: dict[str, FundProfile] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        fund_codes: Iterable[str],
        *,
        profiles: Iterable[FundProfile] = (),
    ) -> "PrimarySectorBatchContext":
        normalized_codes = {
            str(raw or "").strip().zfill(6)
            for raw in fund_codes
            if str(raw or "").strip()
        }
        normalized_codes.discard("000000")
        normalized_codes = {code for code in normalized_codes if len(code) == 6}

        user_rows_by_code: dict[str, dict] = {}
        if normalized_codes:
            try:
                for row in list_fund_primary_sectors():
                    code = str(row.get("fund_code") or "").strip().zfill(6)
                    if code in normalized_codes:
                        user_rows_by_code[code] = row
            except RuntimeError:
                # Background jobs may not have a request-scoped user database.
                pass

        global_rows_by_code = (
            get_fund_primary_sectors_global_by_codes(normalized_codes)
            if normalized_codes
            else {}
        )
        profiles_by_code = {
            profile.fund_code: profile
            for profile in profiles
            if profile.fund_code and profile.fund_code != "000000"
        }
        return cls(
            user_rows_by_code=user_rows_by_code,
            global_rows_by_code=global_rows_by_code,
            profiles_by_code=profiles_by_code,
        )

    @staticmethod
    def _code(raw: str) -> str:
        return str(raw or "").strip().zfill(6)

    def user_row(self, fund_code: str) -> dict | None:
        return self.user_rows_by_code.get(self._code(fund_code))

    def fresh_global_row(self, fund_code: str) -> dict | None:
        row = self.global_rows_by_code.get(self._code(fund_code))
        return row if is_global_sector_fresh(row) else None

    def profile(self, fund_code: str) -> FundProfile | None:
        return self.profiles_by_code.get(self._code(fund_code))

    def remember_user_row(self, row: dict | None) -> None:
        if not row:
            return
        code = self._code(str(row.get("fund_code") or ""))
        if code and code != "000000":
            self.user_rows_by_code[code] = row

    def remember_global_row(self, row: dict | None) -> None:
        if not row:
            return
        code = self._code(str(row.get("fund_code") or ""))
        if code and code != "000000":
            self.global_rows_by_code[code] = row


def _is_cross_market_theme_fund(fund_name: str | None) -> bool:
    if not fund_name:
        return False
    normalized = fund_name.upper()
    return "QDII" in normalized or "全球" in fund_name or "海外" in fund_name


def _is_passive_index_fund_name(fund_name: str | None) -> bool:
    """名称层面的保守识别，仅用于判断指数基准能否充当行情参考。

    这里不靠主题词猜板块；只有明确写出指数/ETF/联接/LOF 的产品，才允许把
    第三方抓取到的精确指数身份用于行情参考。主动基金的业绩比较基准只保留为
    对比基准，不会升级成“关联板块”。
    """
    if not fund_name:
        return False
    normalized = fund_name.upper()
    return any(marker in normalized for marker in ("指数", "ETF", "联接", "LOF"))


def _benchmark_row_is_price_proxy_eligible(
    row: Mapping[str, Any], fund_name: str | None
) -> bool:
    detail = row.get("detail")
    if isinstance(detail, str):
        try:
            parsed_detail = json.loads(detail)
        except json.JSONDecodeError:
            parsed_detail = None
        detail = parsed_detail if isinstance(parsed_detail, Mapping) else None
    if isinstance(detail, Mapping):
        explicit = detail.get("price_proxy_eligible")
        if isinstance(explicit, bool):
            return explicit
        if str(detail.get("benchmark_text_kind") or "") == "tracking_target":
            return True
    return _is_passive_index_fund_name(fund_name)


def _is_fund_name_residue_label(fund_name: str | None, sector_name: str | None) -> bool:
    """判断"关联板块"文本是否只是基金名称本身的营销短语残留，而非真实主题。

    典型场景：总览页 OCR/第三方展示把"中航机遇领航混合发起C"直接截断展示为
    "中航机遇领航"。这类值格式上能通过 `_is_valid_sector_label` 校验（看起来像
    合法短标签），但其实只是基金自身名字的前缀，不是任何行业/主题分类。
    一旦被当作高置信度来源写入，会永久挡住持仓穿透 / LLM 兜底给出的正确结果
    （因为后续所有"当前板块是否已存在"的判断都会认为它"已经有值"而跳过重新推断）。
    """
    if not fund_name or not sector_name:
        return False
    normalized_name = normalize_sector_label(fund_name)
    normalized_sector = normalize_sector_label(sector_name)
    if len(normalized_sector) < 2 or not normalized_name.startswith(normalized_sector):
        return False
    candidate = infer_semantic_sector_from_fund_name(fund_name)
    if candidate is None:
        # 基金名清洗后推不出任何主题（说明这段文字本身就是纯营销/风格短语），
        # 而 sector_name 又恰好是名称前缀 —— 基本可以确定只是名称残留。
        return True
    return (
        candidate.source == "semantic_name_freeform"
        and normalize_sector_label(candidate.sector_name) == normalized_sector
    )


def _is_trustworthy_sector_label(fund_name: str | None, sector_name: str | None) -> bool:
    return bool(_is_valid_sector_label(sector_name)) and not _is_fund_name_residue_label(
        fund_name, sector_name
    )


def _semantic_record_from_candidate(
    code: str,
    fund_name: str,
    candidate,
) -> PrimarySectorRecord:
    return PrimarySectorRecord(
        fund_code=code,
        sector_name=candidate.sector_name,
        intraday_index_name=(
            infer_intraday_index_from_fund_name(fund_name)
            if candidate.quote_key
            else None
        ),
        source=candidate.source or "semantic_name",
        confidence=candidate.confidence,
    )


def _usable_intraday_index_name(
    intraday_index_name: str | None, sector_name: str | None
) -> str | None:
    """业绩基准原文抠出来的指数名（如"中证高端装备制造指数"）大多不在行情源的别名表
    里，前端详情页分时图会拿它直接当查询 key，查不到行情就一直显示"暂无分时数据"——
    而对应的板块短名（如"机械设备"）往往已经注册过行情源。这里在写入前就检查一次，
    查不到行情源、又有更可靠的板块短名可用时，直接不落这个查不到数据的指数名，让所有
    下游消费者（列表日涨幅、详情页分时图）都统一退回板块短名，而不是把这个"死路"一样
    的指数名一直传下去。"""
    if not intraday_index_name:
        return intraday_index_name
    if get_canonical_sector(intraday_index_name) is not None:
        return intraday_index_name
    if sector_name and get_canonical_sector(sector_name) is not None:
        return None
    return intraday_index_name


def _should_prefer_semantic_before_market_sources(fund_name: str | None, candidate) -> bool:
    if candidate is None:
        return False
    if not _is_cross_market_theme_fund(fund_name):
        return False
    return float(candidate.confidence or 0) >= 0.55


def _record_should_override_holding_sector(holding: Holding, record: PrimarySectorRecord) -> bool:
    semantic_candidate = (
        infer_semantic_sector_from_fund_name(holding.fund_name) if holding.fund_name else None
    )
    prefer_semantic = _should_prefer_semantic_before_market_sources(
        holding.fund_name, semantic_candidate
    )
    if (
        prefer_semantic
        and record.source in {"benchmark_index", "benchmark_freeform"}
        and semantic_candidate is not None
        and normalize_sector_label(record.sector_name)
        != normalize_sector_label(semantic_candidate.sector_name)
    ):
        # 跨市场主题基金（QDII/全球/海外）：业绩基准抠出来的往往只是境内细分行业
        # （如"机械设备"），不如基金自身名称主题（如"全球高端制造"）准确，不应该
        # 用它反复覆盖/抢占已经更贴切的主题标签，否则两个来源会来回"打架"。
        return False
    if record.source in {"manual", "ocr_detail", "benchmark_index", "benchmark_freeform"}:
        return True
    if (
        record.source in {"semantic_name", "semantic_name_freeform"}
        and prefer_semantic
        and float(record.confidence or 0) >= 0.55
    ):
        return True
    # 其余来源（holdings_infer / llm_infer / semantic_name_freeform 等）：只有确实比
    # "当前 sector_name 记录在案的来源"更可信时才覆盖。这样持仓穿透/LLM 兜底（结合重仓股）
    # 才能纠正历史上由 alipay_overview、freeform 猜测等低置信度来源写入、但格式上
    # "看起来合法"从而被 _is_valid_sector_label 永久放行的错误标签
    # （例如把"中航机遇领航"这种基金自身营销短语误当成板块）。
    if holding.fund_code and holding.fund_code != "000000":
        existing_row = get_fund_primary_sector(holding.fund_code)
        existing_source = str(existing_row.get("source") or "") if existing_row else ""
        existing_sector_name = existing_row.get("sector_name") if existing_row else None
    else:
        existing_source = ""
        existing_sector_name = None
    new_priority = _SOURCE_PRIORITY.get(record.source, 0)
    old_priority = (
        _effective_priority(existing_source, existing_sector_name, holding.fund_name)
        if existing_source
        else 0
    )
    return new_priority > old_priority


def repair_stale_cross_market_sector(holding: Holding) -> Holding:
    """纯内存、零网络/数据库开销地修正 QDII/全球/海外基金的板块名。

    仅依据基金名称做语义推断，用于冷启动快照直出路径（不能有网络往返），
    修正诸如"华夏全球科技先锋混合(QDII)C"被历史 OCR 误记为「电子」等 A 股板块的问题。
    """
    candidate = (
        infer_semantic_sector_from_fund_name(holding.fund_name)
        if _is_cross_market_theme_fund(holding.fund_name)
        else None
    )
    if candidate is None or float(candidate.confidence or 0) < 0.55:
        return holding
    if holding.sector_name == candidate.sector_name:
        return holding
    fields: dict[str, str | None] = {"sector_name": candidate.sector_name}
    if candidate.quote_key:
        fields["intraday_index_name"] = infer_intraday_index_from_fund_name(holding.fund_name)
    return holding.model_copy(update=fields)


def repair_stale_cross_market_sectors(holdings: list[Holding]) -> list[Holding]:
    return [repair_stale_cross_market_sector(item) for item in holdings]


# 名称残留（如"中航机遇领航"）一旦被写成 alipay_overview，会因为数字优先级
# （50）高于 llm_infer/holdings_infer 而永久挡住更可信的纠正结果。这里给残留
# 标签一个远低于 llm_infer 的"有效优先级"，让持仓穿透/LLM 兜底始终能覆盖它。
_RESIDUE_LABEL_EFFECTIVE_PRIORITY = 5


def _effective_priority(
    source: str, sector_name: str | None = None, fund_name: str | None = None
) -> int:
    prio = _SOURCE_PRIORITY.get(source, 0)
    if (
        source not in _HIGH_TRUST_SECTOR_SOURCES
        and source != "manual"
        and _is_fund_name_residue_label(fund_name, sector_name)
    ):
        return min(prio, _RESIDUE_LABEL_EFFECTIVE_PRIORITY)
    return prio


def _can_upsert_primary_sector(
    existing: dict | None, new_source: str, *, fund_name: str | None = None
) -> bool:
    if not existing:
        return True
    old_source = str(existing.get("source") or "")
    old_prio = _effective_priority(old_source, existing.get("sector_name"), fund_name)
    new_prio = _SOURCE_PRIORITY.get(new_source, 0)
    if new_prio > old_prio:
        return True
    if new_source == "benchmark_index" and old_source in {
        "alipay_overview",
        "name_infer",
    }:
        return True
    return new_prio >= old_prio and new_source == old_source


def upsert_primary_sector_from_profile(
    profile: FundProfile,
    *,
    source: str = "ocr_detail",
    batch_context: PrimarySectorBatchContext | None = None,
) -> None:
    if not profile.fund_code or profile.fund_code == "000000":
        return
    if not _is_valid_sector_label(profile.sector_name):
        return
    if source not in _HIGH_TRUST_SECTOR_SOURCES and source != "manual" and _is_fund_name_residue_label(
        profile.fund_name, profile.sector_name
    ):
        # 总览页展示的只是基金名称残留（非真实主题），不值得当作可信来源写入，
        # 避免其数字优先级挡住后续持仓穿透/LLM 兜底给出的正确结果。
        return
    existing = (
        batch_context.user_row(profile.fund_code)
        if batch_context is not None
        else get_fund_primary_sector(profile.fund_code)
    )
    if existing and not _can_upsert_primary_sector(existing, source, fund_name=profile.fund_name):
        return
    saved = save_fund_primary_sector(
        fund_code=profile.fund_code,
        sector_name=profile.sector_name or "",
        intraday_index_name=profile.intraday_index_name,
        source=source,
        confidence=0.95 if source == "ocr_detail" else 0.9,
        detail={"fund_name": profile.fund_name},
    )
    if batch_context is not None:
        batch_context.remember_user_row(saved)


def upsert_primary_sector_from_holding(
    holding: Holding,
    *,
    source: str,
    batch_context: PrimarySectorBatchContext | None = None,
) -> None:
    if not holding.fund_code or holding.fund_code == "000000":
        return
    if not _is_valid_sector_label(holding.sector_name):
        return
    if source not in _HIGH_TRUST_SECTOR_SOURCES and source != "manual" and _is_fund_name_residue_label(
        holding.fund_name, holding.sector_name
    ):
        return
    existing = (
        batch_context.user_row(holding.fund_code)
        if batch_context is not None
        else get_fund_primary_sector(holding.fund_code)
    )
    if existing and not _can_upsert_primary_sector(existing, source, fund_name=holding.fund_name):
        return
    index_name = holding.intraday_index_name
    if not index_name:
        index_name = infer_intraday_index_from_fund_name(holding.fund_name)
    saved = save_fund_primary_sector(
        fund_code=holding.fund_code,
        sector_name=holding.sector_name or "",
        intraday_index_name=index_name,
        source=source,
        confidence=0.88,
        detail={"fund_name": holding.fund_name},
    )
    if batch_context is not None:
        batch_context.remember_user_row(saved)


def resolve_primary_sector(
    fund_code: str,
    *,
    fund_name: str | None = None,
    allow_name_infer: bool = False,
    fetch_benchmark: bool = True,
    fetch_holdings_infer: bool = False,
    batch_context: PrimarySectorBatchContext | None = None,
) -> PrimarySectorRecord | None:
    code = fund_code.strip().zfill(6)
    if len(code) != 6 or code == "000000":
        return None

    # 跨市场主题基金（QDII/全球/海外）的"名称主题优先"判断需要始终生效，不受
    # allow_name_infer 影响——否则慢路径（fetch_holdings_infer=True 时习惯性
    # 传 allow_name_infer=False）算出的业绩基准板块和快路径算出的名称主题会
    # 在两次刷新之间来回打架（如"机械设备"⇄"全球高端制造"反复横跳）。
    # allow_name_infer 仍然用于控制"弱"名称推断（非跨市场基金）的启用与否。
    semantic_candidate = (
        infer_semantic_sector_from_fund_name(fund_name)
        if fund_name and (allow_name_infer or _is_cross_market_theme_fund(fund_name))
        else None
    )

    row = (
        batch_context.user_row(code)
        if batch_context is not None
        else get_fund_primary_sector(code)
    )
    if row and _is_valid_sector_label(row.get("sector_name")):
        source = str(row.get("source") or "")
        if source == "manual":
            return _record_from_row(row)
        if source in _HIGH_TRUST_SECTOR_SOURCES:
            if (
                _should_prefer_semantic_before_market_sources(fund_name, semantic_candidate)
                and row.get("sector_name") != semantic_candidate.sector_name
            ):
                return _semantic_record_from_candidate(code, fund_name or "", semantic_candidate)
            return _record_from_row(row)

    if _should_prefer_semantic_before_market_sources(fund_name, semantic_candidate):
        return _semantic_record_from_candidate(code, fund_name or "", semantic_candidate)

    # 已有/新拉取的持仓穿透证据优先于第三方业绩基准。基准文本对主动基金只是
    # 业绩比较参考，不应抢占“主要关联板块”。
    if row and str(row.get("source") or "") in {"holdings_infer", "precompute_holdings"}:
        if _is_trustworthy_sector_label(fund_name, row.get("sector_name")):
            return _record_from_row(row)

    global_row = (
        batch_context.fresh_global_row(code)
        if batch_context is not None
        else load_fresh_global_sector(code)
    )
    if global_row and str(global_row.get("source") or "") in {
        "holdings_infer",
        "precompute_holdings",
    }:
        return _record_from_row({**global_row, "fund_code": code})

    if fetch_holdings_infer:
        holdings_record = _resolve_from_holdings_infer(
            code,
            persist=bool(try_get_request_user_id()),
            batch_context=batch_context,
        )
        if holdings_record is not None:
            return holdings_record

    benchmark_record = _resolve_from_benchmark_index(
        code,
        fund_name=fund_name,
        fetch=fetch_benchmark,
        batch_context=batch_context,
    )
    if benchmark_record is not None:
        return benchmark_record

    if global_row:
        global_source = str(global_row.get("source") or "")
        if global_source not in {"benchmark_index", "precompute_benchmark"} or (
            _benchmark_row_is_price_proxy_eligible(global_row, fund_name)
        ):
            return _record_from_row({**global_row, "fund_code": code})

    if row and _is_trustworthy_sector_label(fund_name, row.get("sector_name")):
        row_source = str(row.get("source") or "")
        if row_source not in {"benchmark_index", "precompute_benchmark"} or (
            _benchmark_row_is_price_proxy_eligible(row, fund_name)
        ):
            return _record_from_row(row)

    # 存量行是名称残留（如"中航机遇领航"），且规则/持仓穿透都推不出更好结果时，
    # 再给 LLM 兜底一次机会——否则残留标签会在没有 fetch_holdings_infer 的
    # 调用里（如 profile 之外的路径）被反复当作"已经有值"而永远无法纠正。
    if (
        fetch_holdings_infer
        and fund_name
        and row
        and _is_valid_sector_label(row.get("sector_name"))
    ):
        llm_record = _resolve_from_llm_infer(
            code,
            fund_name,
            batch_context=batch_context,
        )
        if llm_record is not None:
            return llm_record

    profile = (
        batch_context.profile(code)
        if batch_context is not None
        else get_fund_profile_by_code(code)
    )
    if profile and _is_valid_sector_label(profile.sector_name):
        # 支付宝总览 OCR 不含可靠板块名，勿用档案里的推断值挡住业绩基准。
        if profile.source != "alipay-overview":
            return PrimarySectorRecord(
                fund_code=code,
                sector_name=profile.sector_name or "",
                intraday_index_name=profile.intraday_index_name,
                source="alipay_overview",
                confidence=0.9,
            )

    if allow_name_infer and fund_name:
        candidate = semantic_candidate
        if candidate is not None:
            return _semantic_record_from_candidate(code, fund_name, candidate)

        inferred = infer_sector_label_from_fund_name(fund_name)
        if inferred and get_canonical_sector(inferred):
            return PrimarySectorRecord(
                fund_code=code,
                sector_name=inferred,
                intraday_index_name=infer_intraday_index_from_fund_name(fund_name),
                source="name_infer",
                confidence=0.35,
            )

    # 规则全部推不出主题时的最后一道兜底：借用 LLM 判断主题短标签。
    # 复用 fetch_holdings_infer 作为"当前调用方愿意接受网络时延"的信号——
    # 与持仓穿透（同样发子进程/网络请求）共享同一开关，不新增参数、也不会
    # 悄悄拖慢默认的冷启动/低时延路径。
    if fetch_holdings_infer and allow_name_infer and fund_name:
        llm_record = _resolve_from_llm_infer(
            code,
            fund_name,
            batch_context=batch_context,
        )
        if llm_record is not None:
            return llm_record
    return None


def primary_sector_fields_for_holding(
    holding: Holding,
    *,
    fallback_code: str | None = None,
    allow_name_infer: bool = False,
    fetch_benchmark: bool = True,
    fetch_holdings_infer: bool = False,
    batch_context: PrimarySectorBatchContext | None = None,
) -> dict[str, str]:
    if _is_valid_sector_label(holding.sector_name):
        return {}
    code = holding.fund_code if holding.fund_code != "000000" else (fallback_code or "")
    if not code or code == "000000":
        return {}
    record = resolve_primary_sector(
        code,
        fund_name=holding.fund_name,
        allow_name_infer=allow_name_infer,
        fetch_benchmark=fetch_benchmark,
        fetch_holdings_infer=fetch_holdings_infer,
        batch_context=batch_context,
    )
    if record is None:
        return {}
    fields: dict[str, str] = {"sector_name": record.sector_name}
    if record.intraday_index_name and not holding.intraday_index_name:
        fields["intraday_index_name"] = record.intraday_index_name
    return fields


def apply_primary_sector_to_holding(
    holding: Holding,
    *,
    fetch_benchmark: bool = True,
    allow_name_infer: bool = True,
    batch_context: PrimarySectorBatchContext | None = None,
) -> Holding:
    return _apply_primary_sector_to_holding_impl(
        holding,
        fetch_benchmark=fetch_benchmark,
        allow_name_infer=allow_name_infer,
        batch_context=batch_context,
    )


def _apply_primary_sector_to_holding_impl(
    holding: Holding,
    *,
    fetch_benchmark: bool = True,
    allow_name_infer: bool = True,
    batch_context: PrimarySectorBatchContext | None = None,
) -> Holding:
    if holding.sector_name and not _is_trustworthy_sector_label(
        holding.fund_name, holding.sector_name
    ):
        holding = holding.model_copy(update={"sector_name": None})

    from app.services.sector_labels import infer_sector_label_from_fund_name

    inferred = infer_sector_label_from_fund_name(holding.fund_name)
    if (
        inferred
        and holding.sector_name == inferred
        and holding.fund_name
        and "指数" in holding.fund_name
    ):
        holding = holding.model_copy(update={"sector_name": None, "intraday_index_name": None})

    code = holding.fund_code if holding.fund_code != "000000" else ""
    record = None
    if code:
        record = resolve_primary_sector(
            code,
            fund_name=holding.fund_name,
            allow_name_infer=allow_name_infer,
            fetch_benchmark=fetch_benchmark,
            batch_context=batch_context,
        )

    if record and _record_should_override_holding_sector(holding, record):
        fields: dict[str, str] = {"sector_name": record.sector_name}
        if record.intraday_index_name:
            fields["intraday_index_name"] = record.intraday_index_name
        if holding.sector_name != record.sector_name or holding.intraday_index_name != record.intraday_index_name:
            updated = holding.model_copy(update=fields)
            upsert_primary_sector_from_holding(
                updated,
                source=record.source,
                batch_context=batch_context,
            )
            return updated

    if _is_valid_sector_label(holding.sector_name):
        if holding.fund_code and holding.fund_code != "000000":
            upsert_primary_sector_from_holding(
                holding,
                source="alipay_overview",
                batch_context=batch_context,
            )
        return holding

    if record is None:
        return holding
    fields = {"sector_name": record.sector_name}
    if record.intraday_index_name and not holding.intraday_index_name:
        fields["intraday_index_name"] = record.intraday_index_name
    updated = holding.model_copy(update=fields)
    upsert_primary_sector_from_holding(
        updated,
        source=record.source,
        batch_context=batch_context,
    )
    return updated


def apply_primary_sector_to_holdings(
    holdings: list[Holding],
    *,
    fetch_benchmark: bool = True,
    batch_context: PrimarySectorBatchContext | None = None,
) -> list[Holding]:
    return [
        apply_primary_sector_to_holding(
            item,
            fetch_benchmark=fetch_benchmark,
            batch_context=batch_context,
        )
        for item in holdings
    ]


def refresh_benchmark_sectors_for_holdings(
    holdings: list[Holding],
    *,
    fetch_missing_benchmark: bool = True,
    fetch_holdings_infer: bool = False,
    batch_context: PrimarySectorBatchContext | None = None,
) -> list[Holding]:
    """板块刷新前：拉业绩基准；仍无板块时可选重仓行业穿透。"""
    refreshed: list[Holding] = []
    for holding in holdings:
        code = (holding.fund_code or "").strip()
        if not code or code == "000000":
            refreshed.append(holding)
            continue
        row = (
            batch_context.user_row(code)
            if batch_context is not None
            else get_fund_primary_sector(code)
        )
        if row and str(row.get("source") or "") in _HIGH_TRUST_SECTOR_SOURCES:
            refreshed.append(holding)
            continue
        if (
            row
            and str(row.get("source") or "") == "benchmark_index"
            and not fetch_holdings_infer
        ):
            refreshed.append(
                apply_primary_sector_to_holding(
                    holding,
                    fetch_benchmark=False,
                    batch_context=batch_context,
                )
            )
            continue
        if not fetch_missing_benchmark and not fetch_holdings_infer:
            refreshed.append(
                apply_primary_sector_to_holding(
                    holding,
                    fetch_benchmark=False,
                    batch_context=batch_context,
                )
            )
            continue
        updated = apply_primary_sector_to_holding(
            holding,
            fetch_benchmark=fetch_missing_benchmark,
            allow_name_infer=not fetch_holdings_infer,
            batch_context=batch_context,
        )
        stocks_for_code = None
        holdings_evidence_for_code = None
        if fetch_holdings_infer:
            from app.services.fund_holdings_sector_infer import (
                fetch_portfolio_stocks_with_industry_evidence,
            )

            holdings_evidence_for_code = fetch_portfolio_stocks_with_industry_evidence(code)
            raw_stocks = holdings_evidence_for_code.get("stocks")
            stocks_for_code = list(raw_stocks) if isinstance(raw_stocks, list) else []
            record = _resolve_from_holdings_infer(
                code,
                persist=True,
                stocks=stocks_for_code,
                evidence_payload=holdings_evidence_for_code,
                batch_context=batch_context,
            )
            if record is not None:
                fields: dict[str, str] = {"sector_name": record.sector_name}
                if record.intraday_index_name and not updated.intraday_index_name:
                    fields["intraday_index_name"] = record.intraday_index_name
                updated = updated.model_copy(update=fields)
        if (
            fetch_holdings_infer
            and not _is_trustworthy_sector_label(updated.fund_name, updated.sector_name)
            and updated.fund_name
        ):
            # 复用上面已经拉取过的重仓股名称，避免同一只基金重复发子进程/网络请求。
            top_holdings = [s.name for s in (stocks_for_code or []) if s.name]
            llm_record = _resolve_from_llm_infer(
                code,
                updated.fund_name,
                top_holdings=top_holdings,
                batch_context=batch_context,
            )
            if llm_record is not None:
                updated = updated.model_copy(update={"sector_name": llm_record.sector_name})
        refreshed.append(updated)
    return refreshed


def recommend_sector_from_holdings(fund_code: str) -> PrimarySectorRecord | None:
    return _resolve_from_holdings_infer(fund_code, persist=True)


def _resolve_from_holdings_infer(
    fund_code: str,
    *,
    persist: bool = True,
    stocks: list | None = None,
    evidence_payload: Mapping[str, Any] | None = None,
    batch_context: PrimarySectorBatchContext | None = None,
) -> PrimarySectorRecord | None:
    from app.services.fund_holdings_sector_infer import (
        assess_sector_from_portfolio_stocks,
        fetch_portfolio_stocks_with_industry_evidence,
    )

    code = fund_code.strip().zfill(6)
    if evidence_payload is None and stocks is None:
        evidence_payload = fetch_portfolio_stocks_with_industry_evidence(code)
        raw_stocks = evidence_payload.get("stocks")
        stocks = list(raw_stocks) if isinstance(raw_stocks, list) else []
    if not stocks:
        return None

    sector_clue = (
        evidence_payload.get("sector_clue")
        if isinstance(evidence_payload, Mapping)
        else None
    )
    if not isinstance(sector_clue, Mapping):
        sector_clue = assess_sector_from_portfolio_stocks(stocks)
    qualification = sector_clue.get("qualification")
    if (
        not isinstance(qualification, Mapping)
        or qualification.get("sector_inference_eligible") is not True
        or qualification.get("research_only") is not False
    ):
        return None

    sector_name = str(sector_clue.get("sector_name") or "").strip()
    scores = sector_clue.get("scores")
    evidence = sector_clue.get("evidence")
    if not sector_name or not isinstance(scores, Mapping) or not isinstance(evidence, list):
        return None
    confidence = min(0.92, round(scores[sector_name] / 100.0 + 0.5, 2))
    from app.services.fund_profile import infer_intraday_index_from_sector

    index_name = infer_intraday_index_from_sector(sector_name)

    record = PrimarySectorRecord(
        fund_code=code,
        sector_name=sector_name,
        intraday_index_name=index_name,
        source="holdings_infer",
        confidence=confidence,
        detail={
            "scores": dict(scores),
            "evidence": evidence[:8],
            "coverage": sector_clue.get("coverage"),
            "qualification": dict(qualification),
        },
    )

    if persist:
        # Background/manual precompute can promote a reusable global result
        # without a request user.  Do not touch the per-user table until a
        # user context actually exists.
        if try_get_request_user_id() is not None:
            existing = (
                batch_context.user_row(code)
                if batch_context is not None
                else get_fund_primary_sector(code)
            )
            if _can_upsert_primary_sector(existing, "holdings_infer"):
                saved = save_fund_primary_sector(
                    fund_code=code,
                    sector_name=sector_name,
                    intraday_index_name=index_name,
                    source="holdings_infer",
                    confidence=confidence,
                    detail=record.detail,
                )
                if batch_context is not None:
                    batch_context.remember_user_row(saved)
        global_row = promote_record_to_global(record)
        if batch_context is not None:
            batch_context.remember_global_row(global_row)
    return record


def _fetch_top_holding_names_for_llm(fund_code: str) -> list[str]:
    """给 LLM 兜底喂前几大重仓股名称（不依赖脆弱的个股行业接口，只要股票名称）。"""
    try:
        from app.services.fund_holdings_sector_infer import fetch_portfolio_stocks_with_industry

        stocks = fetch_portfolio_stocks_with_industry(fund_code)
    except Exception:
        return []
    return [row.name for row in stocks if row.name][:8]


def _resolve_from_llm_infer(
    fund_code: str,
    fund_name: str,
    *,
    top_holdings: list[str] | None = None,
    batch_context: PrimarySectorBatchContext | None = None,
) -> PrimarySectorRecord | None:
    from app.services.fund_sector_llm_infer import infer_sector_via_llm

    code = fund_code.strip().zfill(6)
    global_row = (
        batch_context.fresh_global_row(code)
        if batch_context is not None
        else load_fresh_global_sector(code)
    )
    if global_row and str(global_row.get("source") or "") in {"llm_infer", "precompute_llm"}:
        return _record_from_row({**global_row, "fund_code": code})

    if top_holdings is None:
        top_holdings = _fetch_top_holding_names_for_llm(code)
    result = infer_sector_via_llm(code, fund_name, top_holdings=top_holdings)
    if result is None:
        return None
    sector_name, confidence = result
    record = PrimarySectorRecord(
        fund_code=code,
        sector_name=sector_name,
        intraday_index_name=None,
        source="llm_infer",
        confidence=confidence,
        detail={"fund_name": fund_name},
    )
    if try_get_request_user_id() is not None:
        existing = (
            batch_context.user_row(code)
            if batch_context is not None
            else get_fund_primary_sector(code)
        )
        if _can_upsert_primary_sector(existing, "llm_infer", fund_name=fund_name):
            saved = save_fund_primary_sector(
                fund_code=code,
                sector_name=sector_name,
                intraday_index_name=None,
                source="llm_infer",
                confidence=confidence,
                detail=record.detail,
            )
            if batch_context is not None:
                batch_context.remember_user_row(saved)
    global_saved = promote_record_to_global(record)
    if batch_context is not None:
        batch_context.remember_global_row(global_saved)
    return record


def refresh_primary_sector_for_fund(fund_code: str, *, fund_name: str | None = None) -> dict:
    code = fund_code.strip().zfill(6)
    current = resolve_primary_sector(code, fund_name=fund_name)
    recommendation = recommend_sector_from_holdings(code)
    resolved_after = resolve_primary_sector(
        code,
        fund_name=fund_name,
        fetch_benchmark=False,
    )
    applied = bool(
        recommendation
        and resolved_after
        and resolved_after.source in {"holdings_infer", "precompute_holdings"}
        and resolved_after.sector_name == recommendation.sector_name
    )
    return {
        "fund_code": code,
        "current": _record_to_dict(current),
        "recommendation": _record_to_dict(recommendation),
        "applied": applied,
    }


def sync_primary_sectors_from_profiles(profiles: list[FundProfile]) -> int:
    synced = 0
    for profile in profiles:
        if _is_valid_sector_label(profile.sector_name):
            upsert_primary_sector_from_profile(profile, source="ocr_detail")
            synced += 1
    return synced


def _resolve_from_benchmark_index(
    fund_code: str,
    *,
    fund_name: str | None = None,
    fetch: bool = True,
    persist_user: bool = True,
    promote_global: bool = True,
    preloaded_global_row: dict | None | object = _GLOBAL_ROW_NOT_PRELOADED,
    batch_context: PrimarySectorBatchContext | None = None,
) -> PrimarySectorRecord | None:
    from app.services.fund_benchmark_sector import (
        fetch_fund_benchmark_text,
        get_fund_benchmark_fetch_metadata,
        resolve_sector_from_benchmark,
    )

    if persist_user and try_get_request_user_id() is not None:
        existing = (
            batch_context.user_row(fund_code)
            if batch_context is not None
            else get_fund_primary_sector(fund_code)
        )
        if (
            existing
            and str(existing.get("source") or "") == "benchmark_index"
            and _benchmark_row_is_price_proxy_eligible(existing, fund_name)
        ):
            return _record_from_row(existing)

    global_row = (
        batch_context.fresh_global_row(fund_code)
        if batch_context is not None
        else (
            load_fresh_global_sector(fund_code)
            if preloaded_global_row is _GLOBAL_ROW_NOT_PRELOADED
            else preloaded_global_row
        )
    )
    if global_row:
        global_source = str(global_row.get("source") or "")
        if (
            (not fetch or global_source in {"benchmark_index", "precompute_benchmark"})
            and _benchmark_row_is_price_proxy_eligible(global_row, fund_name)
        ):
            return _record_from_row({**global_row, "fund_code": fund_code.strip().zfill(6)})

    if not fetch:
        return None
    if _benchmark_miss_cached(fund_code):
        return None

    benchmark_text = fetch_fund_benchmark_text(fund_code)
    if not benchmark_text:
        _remember_benchmark_miss(fund_code)
        return None
    resolved = resolve_sector_from_benchmark(benchmark_text)
    benchmark_metadata = get_fund_benchmark_fetch_metadata(fund_code, benchmark_text)
    price_proxy_eligible = (
        str(benchmark_metadata.get("benchmark_text_kind") or "") == "tracking_target"
        or _is_passive_index_fund_name(fund_name)
    )
    if not price_proxy_eligible:
        # 主动基金的业绩比较基准只用于走势对比；没有持仓穿透证据时宁可不展示
        # 单一关联板块，也不把基准涨幅伪装成基金当日涨幅。
        return None
    code = fund_code.strip().zfill(6)
    source = "benchmark_index"
    detail: dict
    confidence = 0.68
    if resolved is not None:
        sector_name, intraday_index_name, match = resolved
        detail = {
            "index_code": match.index_code,
            "index_name": match.index_name,
            "benchmark_text": match.benchmark_text,
            "relation_kind": "tracking_reference",
            "price_proxy_eligible": True,
            **benchmark_metadata,
        }
    else:
        # 未能精确解析指数身份时，不再从基准文案截取一个短语冒充关联板块。
        # 原始基准仍可由只读详情接口展示，但没有可靠代码就不能接行情涨幅。
        _remember_benchmark_miss(fund_code)
        return None

    intraday_index_name = _usable_intraday_index_name(intraday_index_name, sector_name)

    record = PrimarySectorRecord(
        fund_code=code,
        sector_name=sector_name,
        intraday_index_name=intraday_index_name,
        source=source,
        confidence=confidence,
        detail=detail,
    )
    if persist_user and try_get_request_user_id() is not None:
        existing = (
            batch_context.user_row(code)
            if batch_context is not None
            else get_fund_primary_sector(code)
        )
        if _can_upsert_primary_sector(existing, source):
            saved = save_fund_primary_sector(
                fund_code=code,
                sector_name=sector_name,
                intraday_index_name=intraday_index_name,
                source=source,
                confidence=record.confidence,
                detail=record.detail,
            )
            if batch_context is not None:
                batch_context.remember_user_row(saved)
    if promote_global:
        global_saved = promote_record_to_global(record)
        if batch_context is not None:
            batch_context.remember_global_row(global_saved)
    with _benchmark_miss_cache_lock:
        _benchmark_miss_cache.pop(fund_code, None)
    return record


def _benchmark_miss_cached(fund_code: str) -> bool:
    now = datetime.now(timezone.utc)
    with _benchmark_miss_cache_lock:
        _prune_benchmark_miss_cache(now)
        missed_at = _benchmark_miss_cache.get(fund_code)
        if missed_at is None:
            return False
        _benchmark_miss_cache.move_to_end(fund_code)
        return True


def _remember_benchmark_miss(fund_code: str) -> None:
    now = datetime.now(timezone.utc)
    with _benchmark_miss_cache_lock:
        _prune_benchmark_miss_cache(now)
        _benchmark_miss_cache[fund_code] = now
        _benchmark_miss_cache.move_to_end(fund_code)
        while len(_benchmark_miss_cache) > _BENCHMARK_MISS_CACHE_MAX_ENTRIES:
            _benchmark_miss_cache.popitem(last=False)


def _prune_benchmark_miss_cache(now: datetime) -> None:
    expired_codes = [
        code
        for code, missed_at in _benchmark_miss_cache.items()
        if now - missed_at >= _BENCHMARK_MISS_TTL
    ]
    for code in expired_codes:
        _benchmark_miss_cache.pop(code, None)


def _record_from_row(row: dict) -> PrimarySectorRecord:
    detail = row.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            detail = None
    return PrimarySectorRecord(
        fund_code=str(row["fund_code"]),
        sector_name=str(row["sector_name"]),
        intraday_index_name=row.get("intraday_index_name"),
        source=str(row.get("source") or "unknown"),
        confidence=row.get("confidence"),
        detail=detail if isinstance(detail, dict) else None,
    )


def _record_to_dict(record: PrimarySectorRecord | None) -> dict | None:
    if record is None:
        return None
    return {
        "fund_code": record.fund_code,
        "sector_name": record.sector_name,
        "intraday_index_name": record.intraday_index_name,
        "source": record.source,
        "confidence": record.confidence,
        "detail": record.detail,
    }


def primary_sector_row_for_api(fund_code: str, *, fund_name: str | None = None) -> dict:
    record = resolve_primary_sector(fund_code, fund_name=fund_name, fetch_benchmark=True)
    return {
        "fund_code": fund_code.strip().zfill(6),
        "mapping": _record_to_dict(record),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
