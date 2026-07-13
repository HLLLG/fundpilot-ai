"""Dockerfile 未打包 var/factor_ic 目录修复回归（2026-07-04，方案 B）。

根因：`var/` 整体被 `.gitignore` 排除，`factor_confidence.py::load_ic_summary()` 读取
的 `var/factor_ic/summary.json` 从未打进生产镜像——容器里该文件永远不存在，因子分
这一路在线上恒为「不足」（即使 build_factor_scores_payload 的串行超时问题修好，
这一路依然拿不到有效因子置信）。

修复分两层：① Dockerfile 新增 COPY 语句把 var/factor_ic 打进镜像；② 因为该目录
整体被 .gitignore 排除，一次干净 checkout 里这个目录连空目录都不存在，裸 COPY 会
让整个镜像构建失败——所以还需要一个被 git 追踪的 .gitkeep 占位文件撑住这层目录，
且 .gitignore 的排除规则要从"整个目录级排除"改成"目录内容排除 + 显式放行 .gitkeep"
（git 语义：父目录被规则整体匹配排除后，子路径的否定模式不生效，见 gitignore(5)
"It is not possible to re-include a file if a parent directory of that file is
excluded"）。这里不跑真实 docker build（CI 环境未必有 docker），而是用 git 自身
的 check-ignore / ls-tree 机制验证「.gitkeep 会被追踪」「summary.json 仍被排除」
「Dockerfile 的 COPY 源路径与 .gitkeep 所在路径一致」三件事，等价于验证了
COPY 指令在真实 git checkout 场景下不会因源路径不存在而报错。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "apps" / "api"


def _git_check_ignore(*relative_paths: str) -> set[str]:
    """返回 relative_paths 中「被 .gitignore 排除」的子集（相对仓库根目录路径）。"""
    result = subprocess.run(
        ["git", "check-ignore", *relative_paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    ignored = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return {path.replace("\\", "/") for path in ignored}


def test_gitkeep_placeholder_is_not_ignored() -> None:
    """.gitkeep 必须能被 git 追踪，否则一次干净 checkout 里 var/factor_ic/ 目录
    根本不存在，Dockerfile 的 COPY 指令会因源路径不存在而让镜像构建失败。"""
    ignored = _git_check_ignore("apps/api/var/factor_ic/.gitkeep")
    assert "apps/api/var/factor_ic/.gitkeep" not in ignored


def test_generated_summary_json_remains_ignored() -> None:
    """summary.json 本身仍应被 .gitignore 排除——它是运行期生成物，不应入库
    （方案 A 未被采纳；参见 docs/TODO_factor_ic_refresh.md 的方案取舍记录）。"""
    ignored = _git_check_ignore("apps/api/var/factor_ic/summary.json")
    assert "apps/api/var/factor_ic/summary.json" in ignored


def test_unrelated_var_scratch_files_remain_ignored() -> None:
    """var/ 下其余生成物（如 var/amac/*，季报重仓穿透的中间产物）不应被本次
    改动意外放行——.gitignore 规则调整必须是精确到 factor_ic/ 这一层，不能
    误伤其它目录。"""
    ignored = _git_check_ignore("apps/api/var/amac/class1.json")
    assert "apps/api/var/amac/class1.json" in ignored


def test_gitkeep_placeholder_file_exists_on_disk() -> None:
    """占位文件本身必须真实存在于工作区（不是只在 .gitignore 规则里被提及）。"""
    gitkeep = API_ROOT / "var" / "factor_ic" / ".gitkeep"
    assert gitkeep.is_file()


def _read_dockerfile_copy_lines(dockerfile_path: Path) -> list[str]:
    text = dockerfile_path.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip().startswith("COPY")]


def test_apps_api_dockerfile_copies_factor_ic_directory() -> None:
    """apps/api/Dockerfile（docker-compose.cloud.yml 与部署文档手动构建用）
    须显式拷贝 var/factor_ic，且不能是裸 `COPY var /app/var`（源目录整体不存在
    时会报错，只能拷贝 factor_ic 这一层已经有 .gitkeep 撑住的子目录）。"""
    copy_lines = _read_dockerfile_copy_lines(API_ROOT / "Dockerfile")
    factor_ic_copies = [line for line in copy_lines if "var/factor_ic" in line or "var /app/var" in line]
    assert factor_ic_copies, "apps/api/Dockerfile 缺少 var/factor_ic 相关 COPY 指令"
    assert not any(
        line.split()[1] == "var" and "factor_ic" not in line for line in factor_ic_copies
    ), "不应使用裸 `COPY var /app/var`（var/ 目录整体在干净 checkout 里不存在）"


def test_root_dockerfile_copies_factor_ic_directory() -> None:
    """根目录 Dockerfile（CloudBase 自动部署实际读取的入口）同样须打包 var/factor_ic。"""
    copy_lines = _read_dockerfile_copy_lines(REPO_ROOT / "Dockerfile")
    factor_ic_copies = [
        line for line in copy_lines if "var/factor_ic" in line or "apps/api/var /app/var" in line
    ]
    assert factor_ic_copies, "根目录 Dockerfile 缺少 var/factor_ic 相关 COPY 指令"
    assert not any(
        "apps/api/var /app/var" in line for line in factor_ic_copies
    ), "不应使用裸 `COPY apps/api/var /app/var`（该目录整体在干净 checkout 里不存在）"


def test_local_ocr_is_optional_but_remains_enabled_by_default() -> None:
    """云端 VLM 部署可主动瘦身，但默认值必须保留本地 OCR 回退能力。"""
    for dockerfile in (API_ROOT / "Dockerfile", REPO_ROOT / "Dockerfile"):
        text = dockerfile.read_text(encoding="utf-8")
        assert "ARG INSTALL_LOCAL_OCR=true" in text
        assert 'if [ "$INSTALL_LOCAL_OCR" = "true" ]' in text
        assert "requirements-ocr.txt" in text


def test_fresh_checkout_simulation_has_factor_ic_directory_via_git_archive() -> None:
    """用 `git ls-files` 模拟「一次干净 checkout 会实际落地哪些文件」，直接证明
    var/factor_ic/ 这一层目录必然存在（哪怕只有 .gitkeep），而不是仅凭
    check-ignore 的规则推断。这是本文件里最接近「真的验证过 Docker COPY 不会
    失败」的一个断言，且不需要本机安装 Docker。"""
    result = subprocess.run(
        ["git", "ls-files", "apps/api/var"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tracked_files = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    assert "apps/api/var/factor_ic/.gitkeep" in tracked_files
    assert "apps/api/var/factor_ic/summary.json" not in tracked_files
