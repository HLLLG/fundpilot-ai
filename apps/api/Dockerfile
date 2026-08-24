FROM python:3.12-slim

WORKDIR /app

# 截图识别只走云端 qwen-vl-ocr，镜像不再安装本地 PaddleOCR
# （省掉约 550 MiB 依赖和 libgl1/libgomp1 等系统库）。
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
# 每交易日的板块方向状态捕获（.github/workflows/sector-direction-capture.yml 通过
# `docker compose exec api` 调用）。镜像逐个白名单拷贝脚本，漏了这行定时任务会直接
# 报「找不到文件」。
COPY scripts/capture_sector_direction_states.py /app/scripts/capture_sector_direction_states.py
# 板块身份运维脚本（与根目录生产 Dockerfile 保持一致）。
COPY scripts/rescan_cpo_cxo_targets.py /app/scripts/rescan_cpo_cxo_targets.py
COPY scripts/diagnose_sector_vehicles.py /app/scripts/diagnose_sector_vehicles.py
COPY scripts/rerun_holdings_primary_sectors.py /app/scripts/rerun_holdings_primary_sectors.py
COPY scripts/rerun_all_primary_sectors.py /app/scripts/rerun_all_primary_sectors.py
COPY scripts/verify_holdings_primary_sectors.py /app/scripts/verify_holdings_primary_sectors.py

# 2026-07-04（方案 B，见 apps/api/var/factor_ic/.gitkeep 注释）：因子 IC 离线回测
# 产物（scripts/run_factor_ic.py 生成，供 factor_confidence.py::load_ic_summary 读取，
# 给因子分挂可回测置信）此前从未打进镜像——容器里 var/factor_ic/summary.json 永远
# 不存在，导致「量化证据」的因子分量在线上恒为「不足」。`.gitignore` 整个排除了
# apps/api/var/ 的内容，一次干净的 git clone 里这个目录连空目录都不存在，普通
# `COPY var /app/var` 会因源路径不存在直接让整个镜像构建失败。`.gitkeep` 占位文件
# 保证 var/factor_ic/ 这一层目录在任何 checkout 里都存在（同 data/.gitkeep、
# uploads/.gitkeep 的既有模式），因此对该目录做 COPY 永远不会失败，不论
# summary.json 是否真的在场——本机手动构建（docker build 直接读本地文件系统，
# 不受 .gitignore 影响）时该文件存在则会被一并拷入；若生产构建走纯 git clone
# 且该文件确实未被追踪，则该路会诚实降级为「不足」而不是让部署整体挂掉。
COPY var/factor_ic /app/var/factor_ic

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV WEB_CONCURRENCY=1

EXPOSE 8000

# 1 worker + 流式 SSE 走 async（见 async_sse.py）；双流靠进程内线程池，worker 数可由部署环境覆盖。
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
