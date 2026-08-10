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
