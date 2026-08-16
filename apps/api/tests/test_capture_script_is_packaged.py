"""定时任务用到的脚本必须真的进生产镜像。

回归背景（真实故障，已由生产运行证实）：新增
`scripts/capture_sector_direction_states.py` 与它的定时 workflow 之后，手动触发一次
生产运行直接失败：

    python: can't open file '/app/scripts/capture_sector_direction_states.py':
    [Errno 2] No such file or directory

根因是仓库里有**两份** Dockerfile，而生产用的是**根目录**那份
（`docker-compose.production.yml`：`context: .` + `dockerfile: Dockerfile`），当时只改了
`apps/api/Dockerfile`。两份都逐个白名单拷贝脚本（镜像刻意不整目录拷 `scripts/`），所以
漏掉任何一份都会让定时任务每晚静默失败——而失败信息只出现在 Actions 日志里，界面上看不出
任何异常。

这组用例不依赖本机 Docker：只检查两份 Dockerfile 的 COPY 清单与两份 `.dockerignore` 的
白名单，把「新增定时任务脚本时忘了打包」变成一个测试失败而不是一次生产故障。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]

#: 由定时任务/运维通过 `docker compose exec api python scripts/<name>` 调用的脚本。
#: 新增这类脚本时必须同时更新两份 Dockerfile 与两份 .dockerignore。
SCHEDULED_SCRIPTS = (
    "settle_pending_outcomes.py",
    "evaluate_decision_quality.py",
    "capture_sector_direction_states.py",
)


@pytest.mark.parametrize("script", SCHEDULED_SCRIPTS)
def test_script_exists(script: str) -> None:
    assert (API_ROOT / "scripts" / script).is_file()


@pytest.mark.parametrize("script", SCHEDULED_SCRIPTS)
def test_production_dockerfile_copies_the_script(script: str) -> None:
    """根目录 Dockerfile 是生产真正用的那份（context 为仓库根，路径带 apps/api/ 前缀）。"""
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    expected = f"COPY apps/api/scripts/{script} /app/scripts/{script}"
    assert expected in text, (
        f"生产镜像没有拷贝 {script}；定时任务会报 No such file or directory。"
        "注意生产用的是**根目录** Dockerfile，不是 apps/api/Dockerfile。"
    )


@pytest.mark.parametrize("script", SCHEDULED_SCRIPTS)
def test_api_dockerfile_copies_the_script(script: str) -> None:
    text = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    expected = f"COPY scripts/{script} /app/scripts/{script}"
    assert expected in text, f"apps/api/Dockerfile 没有拷贝 {script}"


@pytest.mark.parametrize("script", SCHEDULED_SCRIPTS)
def test_both_dockerignore_allowlists_release_the_script(script: str) -> None:
    """两份 .dockerignore 都用 `scripts/*` 整体排除 + `!` 逐个放行，漏一条就拷不到。"""
    root_lines = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    api_lines = (API_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "apps/api/scripts/*" in root_lines
    assert f"!apps/api/scripts/{script}" in root_lines, (
        f"根 .dockerignore 没有放行 {script}，构建上下文里根本不会包含它"
    )
    assert "scripts/*" in api_lines
    assert f"!scripts/{script}" in api_lines


def test_capture_workflow_invokes_the_packaged_path() -> None:
    """workflow 里的调用路径必须与镜像里的落地路径一致。"""
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "sector-direction-capture.yml"
    ).read_text(encoding="utf-8")
    assert "python scripts/capture_sector_direction_states.py" in workflow
    # 容器 WORKDIR 是 /app，脚本落在 /app/scripts/，因此调用侧必须是相对路径而不是
    # apps/api/scripts/...（后者在容器里不存在）。
    assert "apps/api/scripts/capture_sector_direction_states.py" not in workflow


def test_outcome_settlement_workflow_keeps_long_ssh_sessions_alive() -> None:
    """结算必须走脱离 SSH 的 compose exec helper：握手 RST 要重试，空闲 NAT
    掐线也不能带走 docker exec。"""
    helper = "scripts/ci/run-lighthouse-compose-exec.sh"
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "outcome-settlement.yml"
    ).read_text(encoding="utf-8")
    helper_text = (REPO_ROOT / helper).read_text(encoding="utf-8")
    assert helper in workflow
    assert "group: fundpilot-lighthouse-production" in workflow
    assert workflow.count(helper) == 2
    assert "python -u scripts/settle_pending_outcomes.py" in workflow
    assert "python -u scripts/evaluate_decision_quality.py" in workflow
    assert "apps/api/scripts/settle_pending_outcomes.py" not in workflow
    assert "ConnectTimeout=15" in helper_text
    assert "IPQoS=none" in helper_text
    assert "TCPKeepAlive=yes" in helper_text
    assert "ServerAliveCountMax=10" in helper_text
    assert "setsid --fork" in helper_text
    assert "PYTHONUNBUFFERED=1" in helper_text
    assert "/srv/fundpilot/deploy.lock" in helper_text


# ---------------------------------------------------------------------------
# 同一张表的 schema 有**两处**维护点：SQLite 走 db_migrations，生产 MySQL 走
# mysql_bootstrap。第二次生产实测踩的就是这个——脚本已在镜像里、取数各段都跑完，
# 但 MySQL 缺列导致落库与回读双双静默失败（两处都是 best-effort try/except），
# 摘要里只剩 None，从输出完全看不出是缺列。
# ---------------------------------------------------------------------------

#: 退出侧 2026-08 新增、两处 schema 都必须有的列。
EXIT_SIDE_COLUMNS = ("trend_evidence_coverage", "source")


@pytest.mark.parametrize("column", EXIT_SIDE_COLUMNS)
def test_sqlite_migration_adds_the_column(column: str) -> None:
    text = (API_ROOT / "app" / "db_migrations.py").read_text(encoding="utf-8")
    assert (
        f'ALTER TABLE sector_direction_states ADD COLUMN {column}' in text
    ), f"SQLite 迁移没有加 {column}"


@pytest.mark.parametrize("column", EXIT_SIDE_COLUMNS)
def test_mysql_bootstrap_creates_and_backfills_the_column(column: str) -> None:
    """新库靠 CREATE TABLE 带上，存量库靠 ALTER 补——两者都要有。

    `CREATE TABLE IF NOT EXISTS` 对已存在的表什么都不做，所以只改建表语句对生产
    （表早就存在）完全无效。
    """
    text = (API_ROOT / "app" / "mysql_bootstrap.py").read_text(encoding="utf-8")
    create_index = text.find("CREATE TABLE IF NOT EXISTS sector_direction_states")
    assert create_index > 0, "找不到 MySQL 侧的建表语句"
    create_block = text[create_index : create_index + 2000]
    assert column in create_block, f"MySQL 建表语句里没有 {column}"
    assert (
        '"sector_direction_states": {' in text
    ), "MySQL 侧缺少 sector_direction_states 的 ALTER 补列配置，存量库不会加列"


def test_mysql_bootstrap_backfills_source_for_existing_rows() -> None:
    """存量行必须与 SQLite 侧同口径地标为 captured，否则发现基金的滞回会把它们过滤掉。"""
    text = (API_ROOT / "app" / "mysql_bootstrap.py").read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    assert (
        "UPDATE sector_direction_states SET source = 'captured' WHERE source IS NULL"
        in normalized
    )
