# CloudBase 自动部署从仓库根目录读取 Dockerfile（monorepo 入口）
FROM python:3.12-slim

WORKDIR /app

ARG INSTALL_LOCAL_OCR=true

RUN sed -i 's|deb.debian.org/debian|mirrors.tuna.tsinghua.edu.cn/debian|g' \
      /etc/apt/sources.list.d/debian.sources \
    && if [ "$INSTALL_LOCAL_OCR" = "true" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY apps/api/requirements.txt apps/api/requirements-ocr.txt /app/
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" \
      -r /app/requirements.txt \
    && if [ "$INSTALL_LOCAL_OCR" = "true" ]; then \
      pip install --no-cache-dir --index-url "$PIP_INDEX_URL" \
        -r /app/requirements-ocr.txt; \
    fi

COPY apps/api/app /app/app
COPY apps/api/scripts/settle_pending_outcomes.py /app/scripts/settle_pending_outcomes.py

# 2026-07-04（方案 B，见 apps/api/var/factor_ic/.gitkeep 注释）：因子 IC 离线回测
# 产物（scripts/run_factor_ic.py 生成，供 factor_confidence.py::load_ic_summary 读取，
# 给因子分挂可回测置信）此前从未打进镜像——容器里 var/factor_ic/summary.json 永远
# 不存在，导致「量化证据」的因子分量在线上恒为「不足」。`.gitkeep` 占位文件保证
# var/factor_ic/ 这一层目录在任何 checkout 里都存在，因此对该目录做 COPY 永远不会
# 因目录缺失而失败；summary.json 是否真的在场则决定这一路是否可用，缺失时诚实
# 降级为「不足」，不会让整个部署构建失败。CloudBase 从本仓库拉代码构建时同样只
# 会拿到已入库的 .gitkeep（summary.json 被 .gitignore 排除），故该路径长期需要
# 方案 C（定期重新生成并同步这份数据）才能在生产环境真正可用，见该 .gitkeep 文件。
COPY apps/api/var/factor_ic /app/var/factor_ic

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV FUND_AI_OCR_PRELOAD=false
ENV DISABLE_MODEL_SOURCE_CHECK=True

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
