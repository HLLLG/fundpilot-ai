"""Single-attempt background challenger execution for D5.1."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models import InvestorProfile, NewsItem, TopicBrief
from app.services.deepseek_http import ProviderOutputError
from app.services.deepseek_streaming import stream_chat_completion
from app.services.discovery_client import DiscoveryClient, build_discovery_report_from_parsed
from app.services.discovery_judge import judge_parsed_discovery_report
from app.services.deepseek_client import _is_valid_discovery_report_payload, _parse_model_json
from app.services.decision_repository import (
    canonical_hash,
    get_decision_quality_artifact_receipt,
    get_decision_quality_input_artifact,
)
from app.services.prompt_shadow_contracts import (
    PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
    PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
    PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES,
    build_prompt_shadow_attempt,
    build_prompt_shadow_output,
    decision_projection_hash,
)
from app.services.prompt_shadow_repository import (
    advance_prompt_shadow_budget,
    finalize_prompt_shadow_challenger,
    get_prompt_shadow_run,
    lease_prompt_shadow_run,
    list_prompt_shadow_worker_candidates,
    transition_prompt_shadow_run,
)
from app.services.prompt_shadow_service import (
    _artifact_receipt_ref,
    _candidate_audit_ref,
    _put_receipted_artifact,
    build_prompt_shadow_projection,
)
from app.services.provider_call_trace import ProviderCallTraceCollector


logger = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _inner_artifact(*, user_id: int, artifact_id: str) -> dict[str, Any]:
    stored = get_decision_quality_input_artifact(
        user_id=user_id,
        artifact_id=artifact_id,
    )
    if stored is None or not isinstance(stored.get("payload"), Mapping):
        raise ValueError("prompt-shadow artifact is missing")
    inner = stored["payload"].get("artifact")
    if not isinstance(inner, Mapping):
        raise ValueError("prompt-shadow inner artifact is missing")
    return dict(inner)


def _receipt_ref(*, user_id: int, artifact_id: str) -> dict[str, Any]:
    stored = get_decision_quality_input_artifact(
        user_id=user_id,
        artifact_id=artifact_id,
    )
    receipt = get_decision_quality_artifact_receipt(
        user_id=user_id,
        artifact_id=artifact_id,
    )
    if (
        stored is None
        or receipt is None
        or not isinstance(stored.get("payload"), Mapping)
    ):
        raise ValueError("prompt-shadow artifact receipt is pending")
    return _artifact_receipt_ref(
        user_id=user_id,
        envelope=stored["payload"],
        receipt_row=receipt,
    )


def _attempt_ref(attempt: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {"attempt_hash": attempt["attempt_hash"], **dict(receipt)}


def _safe_error_parse_status(trace: Mapping[str, Any]) -> str:
    category = str(trace.get("error_category") or "")
    if category == "empty_content":
        return "empty"
    if trace.get("outcome") == "timeout":
        return "timeout"
    if trace.get("outcome") == "http_error":
        return "http_error"
    if trace.get("outcome") == "interrupted":
        return "truncated"
    return "provider_error" if trace.get("outcome") != "success" else "invalid"


def _build_challenger_report(
    *,
    registration: Mapping[str, Any],
    parsed: dict[str, Any],
) -> Any:
    context = registration["guard_context"]
    candidate_pool = deepcopy(context["candidate_pool"])
    discovery_facts = deepcopy(context["discovery_facts"])
    parsed, _judge_meta = judge_parsed_discovery_report(
        parsed,
        candidate_pool=candidate_pool,
        discovery_facts=discovery_facts,
        analysis_mode="fast",
    )
    return build_discovery_report_from_parsed(
        parsed,
        target_sectors=list(context["target_sectors"]),
        focus_sectors=list(context["focus_sectors"]),
        scan_mode="full_market",
        candidate_pool=candidate_pool,
        discovery_facts=discovery_facts,
        profile=InvestorProfile.model_validate(context["profile"]),
        held_codes=set(context["held_codes"]),
        budget_yuan=float(context["requested_budget_yuan"]),
        sector_heat=deepcopy(context["sector_heat"]),
        market_news=[NewsItem.model_validate(item) for item in context["market_news"]],
        topic_briefs=[TopicBrief.model_validate(item) for item in context["topic_briefs"]],
        analysis_mode="fast",
        provider_model=str(
            registration["prompt_pair"]["challenger_provider_payload"]["model"]
        ),
        decision_at=datetime.fromisoformat(registration["decision_at"]),
    )


def _persist_challenger_output(
    *,
    user_id: int,
    run: Mapping[str, Any],
    registration: Mapping[str, Any],
    attempt: Mapping[str, Any],
    attempt_ref: Mapping[str, Any],
    trace: Mapping[str, Any],
    raw_content: str | None,
    parsed_payload: dict[str, Any] | None,
    parse_status: str,
    report: Any | None,
    materialized_at: datetime,
) -> dict[str, Any]:
    raw_bytes = int(trace["content_bytes"])
    if raw_bytes > PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES:
        stored_raw = None
        final_status = "oversize"
        parsed = None
        parsed_hash = None
        error_category = "oversize"
        report = None
    else:
        stored_raw = raw_content
        final_status = parse_status
        parsed = parsed_payload
        parsed_hash = canonical_hash(parsed) if parsed is not None else None
        error_category = None if final_status in {"valid", "interrupted_salvaged"} else (
            trace.get("error_category") or "provider_output_error"
        )
    successful = final_status in {"valid", "interrupted_salvaged"}
    projection = (
        build_prompt_shadow_projection(
            report=report,
            requested_budget_yuan=float(
                registration["guard_context"]["requested_budget_yuan"]
            ),
        )
        if successful and report is not None
        else None
    )
    completed_at = datetime.fromisoformat(str(trace["completed_at"]))
    if materialized_at < completed_at:
        materialized_at = completed_at
    output = build_prompt_shadow_output(
        {
            "schema_version": PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
            "run_id": registration["run_id"],
            "role": "challenger",
            "decision_at": registration["decision_at"],
            "policy_ref": attempt["policy_ref"],
            "registration_ref": attempt["registration_ref"],
            "attempt_ref": dict(attempt_ref),
            "champion_report_id": run["champion_report_id"],
            "variant_report_id": None,
            "candidate_audit_ref": _candidate_audit_ref(
                user_id=user_id,
                report_id=str(run["champion_report_id"]),
            ),
            "trace": dict(trace),
            "response": {
                "raw_content": stored_raw,
                "raw_content_sha256": trace["content_sha256"],
                "raw_content_bytes": raw_bytes,
                "parse_status": final_status,
                "parsed_payload": parsed,
                "parsed_payload_hash": parsed_hash,
                "error_category": error_category,
            },
            "final_projection": projection,
            "final_projection_hash": (
                projection["projection_hash"] if projection is not None else None
            ),
            "decision_projection_hash": (
                decision_projection_hash(projection) if projection is not None else None
            ),
            "output_materialized_at": materialized_at.isoformat(),
            "automatic_promotion_allowed": False,
        },
        registration=registration,
        attempt=attempt,
        expected_user_id=user_id,
    )
    envelope, _receipt = _put_receipted_artifact(user_id=user_id, artifact=output)
    current = get_prompt_shadow_run(user_id=user_id, run_id=str(run["run_id"]))
    if current is None or current["status"] != "challenger_call_started":
        raise ValueError("challenger output no longer owns its run")
    current = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=str(run["run_id"]),
        expected_status=current["status"],
        expected_state_version=current["state_version"],
        new_status="challenger_output_pending_receipt",
        updated_at=materialized_at.isoformat(),
        updates={"challenger_output_artifact_id": envelope["artifact_id"]},
    )
    return current


def _finalize_output_pending(
    *,
    user_id: int,
    run: Mapping[str, Any],
    successful: bool,
    now: datetime,
) -> dict[str, Any]:
    status = "completed" if successful else "challenger_failed"
    reason = "paired_output_receipted" if successful else "challenger_output_failed"
    if run.get("status") == status:
        return dict(run)
    return finalize_prompt_shadow_challenger(
        user_id=user_id,
        run_id=str(run["run_id"]),
        expected_status="challenger_output_pending_receipt",
        expected_state_version=int(run["state_version"]),
        budget_action="completed" if successful else "failed",
        new_status=status,
        updated_at=now.isoformat(),
        terminal_reason=reason,
    )


def _recover_output_pending(*, user_id: int, run: Mapping[str, Any]) -> dict[str, Any]:
    artifact_id = str(run.get("challenger_output_artifact_id") or "")
    if not artifact_id:
        raise ValueError("prompt-shadow output-pending run has no output artifact")
    output = _inner_artifact(user_id=user_id, artifact_id=artifact_id)
    response = output.get("response")
    parse_status = response.get("parse_status") if isinstance(response, Mapping) else None
    return _finalize_output_pending(
        user_id=user_id,
        run=run,
        successful=parse_status in {"valid", "interrupted_salvaged"},
        now=_now(),
    )


def process_prompt_shadow_run(
    *,
    user_id: int,
    run_id: str,
    worker_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute at most one challenger provider request for a champion run."""

    settings = get_settings()
    acquired = (now or _now()).astimezone(timezone.utc)
    run = get_prompt_shadow_run(user_id=user_id, run_id=run_id)
    if run is None or run["status"] != "champion_succeeded":
        return {"status": "not_leaseable", "run": run}
    registration = _inner_artifact(
        user_id=user_id,
        artifact_id=str(run["registration_artifact_id"]),
    )
    policy_artifact_id = str(registration["policy_ref"]["artifact_id"])
    policy = _inner_artifact(user_id=user_id, artifact_id=policy_artifact_id)
    deadline = datetime.fromisoformat(str(run["challenger_deadline_at"]))
    if acquired >= deadline:
        terminal = transition_prompt_shadow_run(
            user_id=user_id,
            run_id=run_id,
            expected_status=run["status"],
            expected_state_version=run["state_version"],
            new_status="challenger_timed_out",
            updated_at=acquired.isoformat(),
            updates={"terminal_reason": "challenger_deadline_elapsed"},
        )
        return {"status": terminal["status"], "run": terminal}
    lease_expiry = min(
        acquired + timedelta(seconds=settings.prompt_shadow_lease_seconds),
        deadline,
    )
    owner_hash = hashlib.sha256(worker_id.encode("utf-8")).hexdigest()
    token_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    lease = lease_prompt_shadow_run(
        user_id=user_id,
        run_id=run_id,
        expected_state_version=run["state_version"],
        lease_owner_hash=owner_hash,
        lease_token_hash=token_hash,
        lease_acquired_at=acquired.isoformat(),
        lease_expires_at=lease_expiry.isoformat(),
        scope_key=str(policy["budget"]["scope_key"]),
        budget_date_local=acquired.astimezone(_SHANGHAI).date().isoformat(),
        policy_id=str(policy["policy_id"]),
        policy_hash=str(policy["policy_hash"]),
        max_calls=int(policy["budget"]["max_challenger_calls_per_day"]),
    )
    if not lease["reserved"]:
        return {"status": lease["run"]["status"], "run": lease["run"]}
    run = lease["run"]
    preregistered = _now()
    if preregistered > lease_expiry:
        terminal = transition_prompt_shadow_run(
            user_id=user_id,
            run_id=run_id,
            expected_status=run["status"],
            expected_state_version=run["state_version"],
            new_status="challenger_timed_out",
            updated_at=preregistered.isoformat(),
            updates={"terminal_reason": "challenger_lease_elapsed_pre_network"},
        )
        return {"status": terminal["status"], "run": terminal}
    registration_ref = _receipt_ref(
        user_id=user_id,
        artifact_id=str(run["registration_artifact_id"]),
    )
    attempt = build_prompt_shadow_attempt(
        {
            "schema_version": PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
            "run_id": run_id,
            "role": "challenger",
            "attempt_number": 1,
            "decision_at": registration["decision_at"],
            "policy_ref": registration["policy_ref"],
            "registration_ref": {
                "registration_hash": registration["registration_hash"],
                **registration_ref,
            },
            "provider": "deepseek",
            "operation": "chat_completions",
            "endpoint_base_url": settings.deepseek_base_url,
            "provider_payload_hash": registration["prompt_pair"][
                "challenger_provider_payload_hash"
            ],
            "transport": registration["prompt_pair"]["transport"],
            "pre_network_registered_at": preregistered.isoformat(),
            "lease": {
                "owner_hash": run["lease_owner_hash"],
                "token_hash": run["lease_token_hash"],
                "acquired_at": run["lease_acquired_at"],
                "expires_at": run["lease_expires_at"],
            },
            "budget_reservation": {
                "scope_key": run["budget_scope_key"],
                "budget_date_local": run["budget_date_local"],
                "policy_hash": run["policy_hash"],
                "max_calls": int(policy["budget"]["max_challenger_calls_per_day"]),
                "reserved_ordinal": int(lease["ordinal"]),
                "reserved_at": run["budget_reserved_at"],
            },
            "automatic_promotion_allowed": False,
        },
        registration=registration,
        expected_user_id=user_id,
    )
    attempt_envelope, attempt_receipt = _put_receipted_artifact(
        user_id=user_id,
        artifact=attempt,
    )
    run = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run_id,
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="challenger_attempt_pending_receipt",
        updated_at=preregistered.isoformat(),
        updates={"challenger_attempt_artifact_id": attempt_envelope["artifact_id"]},
    )
    run = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run_id,
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="challenger_ready",
        updated_at=preregistered.isoformat(),
    )
    network_started = _now()
    if network_started > deadline or network_started > lease_expiry:
        terminal = transition_prompt_shadow_run(
            user_id=user_id,
            run_id=run_id,
            expected_status=run["status"],
            expected_state_version=run["state_version"],
            new_status="challenger_timed_out",
            updated_at=network_started.isoformat(),
            updates={"terminal_reason": "challenger_deadline_elapsed_pre_network"},
        )
        return {"status": terminal["status"], "run": terminal}
    run = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run_id,
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="challenger_call_started",
        updated_at=network_started.isoformat(),
        updates={"challenger_network_started_at": network_started.isoformat()},
    )
    advance_prompt_shadow_budget(
        scope_key=str(run["budget_scope_key"]),
        budget_date_local=str(run["budget_date_local"]),
        action="started",
        updated_at=network_started.isoformat(),
    )

    provider_payload = registration["prompt_pair"]["challenger_provider_payload"]
    messages = provider_payload["messages"]
    transport = str(registration["prompt_pair"]["transport"])
    collector = ProviderCallTraceCollector(transport=transport)
    raw_content: str | None = None
    parsed: dict[str, Any] | None = None
    report = None
    try:
        if transport == "sync":
            client = DiscoveryClient()
            parsed = client._call_model(
                str(messages[0]["content"]),
                registration["prompt_pair"]["user_payload"],
                str(provider_payload["model"]),
                trace_collector=collector,
                exact_provider_payload=provider_payload,
            )
            raw_content = client._last_report_raw_content
        else:
            chunks: list[str] = []
            for chunk in stream_chat_completion(
                messages=messages,
                model=str(provider_payload["model"]),
                max_tokens=int(provider_payload["max_tokens"]),
                response_format=provider_payload["response_format"],
                trace_collector=collector,
                exact_provider_payload=provider_payload,
            ):
                chunks.append(chunk)
                # Preserve an exact partial response if the next iterator step
                # raises; the trace and stored bytes must remain consistent.
                raw_content = "".join(chunks)
            raw_content = raw_content or ""
            parsed = _parse_model_json(raw_content)
            if parsed.get("_truncated") or not _is_valid_discovery_report_payload(parsed):
                raise ProviderOutputError("invalid_json")
        report = _build_challenger_report(registration=registration, parsed=parsed)
        pending = _persist_challenger_output(
            user_id=user_id,
            run=run,
            registration=registration,
            attempt=attempt,
            attempt_ref=_attempt_ref(attempt, attempt_receipt),
            trace=collector.require_trace(),
            raw_content=raw_content,
            parsed_payload=parsed,
            parse_status="valid",
            report=report,
            materialized_at=_now(),
        )
        terminal = _finalize_output_pending(
            user_id=user_id,
            run=pending,
            successful=True,
            now=_now(),
        )
        return {"status": terminal["status"], "run": terminal}
    except Exception:  # noqa: BLE001 - no retry after call_started
        logger.exception(
            "prompt-shadow challenger failed",
            extra={"user_id": user_id, "run_id": run_id},
        )
        current = get_prompt_shadow_run(user_id=user_id, run_id=run_id)
        if current is not None and current["status"] in {
            "completed",
            "challenger_failed",
            "challenger_indeterminate",
        }:
            return {"status": current["status"], "run": current}
        if current is not None and current["status"] == "challenger_output_pending_receipt":
            try:
                terminal = _recover_output_pending(user_id=user_id, run=current)
                return {"status": terminal["status"], "run": terminal}
            except Exception:
                logger.exception(
                    "prompt-shadow output-pending recovery failed",
                    extra={"user_id": user_id, "run_id": run_id},
                )
                return {"status": "recovery_pending", "run": current}
        trace = collector.trace
        try:
            if trace is not None:
                pending = _persist_challenger_output(
                    user_id=user_id,
                    run=run,
                    registration=registration,
                    attempt=attempt,
                    attempt_ref=_attempt_ref(attempt, attempt_receipt),
                    trace=trace,
                    raw_content=raw_content,
                    parsed_payload=None,
                    parse_status=_safe_error_parse_status(trace),
                    report=None,
                    materialized_at=_now(),
                )
                terminal = _finalize_output_pending(
                    user_id=user_id,
                    run=pending,
                    successful=False,
                    now=_now(),
                )
            else:
                current = get_prompt_shadow_run(user_id=user_id, run_id=run_id)
                if current is None or current["status"] != "challenger_call_started":
                    raise
                terminal = finalize_prompt_shadow_challenger(
                    user_id=user_id,
                    run_id=run_id,
                    expected_status=current["status"],
                    expected_state_version=current["state_version"],
                    budget_action="failed",
                    new_status="challenger_indeterminate",
                    updated_at=_now().isoformat(),
                    terminal_reason="challenger_network_outcome_indeterminate",
                )
            return {"status": terminal["status"], "run": terminal}
        except Exception:
            logger.exception(
                "prompt-shadow challenger recovery remains pending",
                extra={"user_id": user_id, "run_id": run_id},
            )
            return {"status": "recovery_pending", "run": run}


def reconcile_prompt_shadow_stale_runs(*, now: datetime | None = None) -> int:
    """Terminalize expired states without ever replaying a possible request."""

    cutoff = (now or _now()).astimezone(timezone.utc)
    rows = list_prompt_shadow_worker_candidates(
        statuses=(
            "champion_call_started",
            "challenger_leased",
            "challenger_attempt_pending_receipt",
            "challenger_ready",
            "challenger_call_started",
            "challenger_output_pending_receipt",
        ),
        limit=1_000,
    )
    changed = 0
    for run in rows:
        try:
            status = str(run["status"])
            if status == "challenger_output_pending_receipt":
                _recover_output_pending(user_id=int(run["userId"]), run=run)
                changed += 1
                continue
            if status == "champion_call_started":
                stale_at = datetime.fromisoformat(run["updated_at"]) + timedelta(
                    seconds=max(900, int(get_settings().deepseek_timeout_seconds) + 300)
                )
                if cutoff <= stale_at:
                    continue
                new_status = "champion_indeterminate"
                reason = "champion_network_outcome_indeterminate"
            else:
                expires = datetime.fromisoformat(str(run["lease_expires_at"]))
                if cutoff <= expires:
                    continue
                if status == "challenger_call_started":
                    finalize_prompt_shadow_challenger(
                        user_id=int(run["userId"]),
                        run_id=str(run["run_id"]),
                        expected_status=status,
                        expected_state_version=int(run["state_version"]),
                        budget_action="failed",
                        new_status="challenger_indeterminate",
                        updated_at=cutoff.isoformat(),
                        terminal_reason="challenger_network_outcome_indeterminate",
                    )
                    changed += 1
                    continue
                else:
                    new_status = "challenger_timed_out"
                    reason = "challenger_lease_elapsed_pre_network"
            transition_prompt_shadow_run(
                user_id=int(run["userId"]),
                run_id=str(run["run_id"]),
                expected_status=status,
                expected_state_version=int(run["state_version"]),
                new_status=new_status,
                updated_at=cutoff.isoformat(),
                updates={"terminal_reason": reason},
            )
            changed += 1
        except Exception:
            logger.exception("prompt-shadow stale run reconciliation failed")
    return changed


def prompt_shadow_worker_loop() -> None:
    settings = get_settings()
    if not (
        settings.prompt_shadow_enabled
        and settings.prompt_shadow_assignment_secret
        and settings.deepseek_configured
    ):
        return
    worker_id = f"prompt-shadow-{secrets.token_hex(12)}"
    while True:
        try:
            reconcile_prompt_shadow_stale_runs()
            rows = list_prompt_shadow_worker_candidates(
                statuses=("champion_succeeded",),
                limit=max(1, min(settings.prompt_shadow_worker_batch_size, 100)),
            )
            for row in rows:
                process_prompt_shadow_run(
                    user_id=int(row["userId"]),
                    run_id=str(row["run_id"]),
                    worker_id=worker_id,
                )
        except Exception:
            logger.exception("prompt-shadow worker iteration failed")
        time.sleep(15.0)


__all__ = [
    "process_prompt_shadow_run",
    "prompt_shadow_worker_loop",
    "reconcile_prompt_shadow_stale_runs",
]
