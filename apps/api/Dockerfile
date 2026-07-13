FROM python:3.12-slim

WORKDIR /app

ARG INSTALL_LOCAL_OCR=true

RUN if [ "$INSTALL_LOCAL_OCR" = "true" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY requirements.txt requirements-ocr.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && if [ "$INSTALL_LOCAL_OCR" = "true" ]; then \
      pip install --no-cache-dir -r /app/requirements-ocr.txt; \
    fi

COPY app /app/app
COPY scripts/settle_pending_outcomes.py /app/scripts/settle_pending_outcomes.py

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
ENV FUND_AI_OCR_PRELOAD=false
ENV DISABLE_MODEL_SOURCE_CHECK=True

EXPOSE 8000

# 2 workers + 流式 SSE 走 async（见 async_sse.py），避免长连接占满 worker 导致 holdings 504
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
