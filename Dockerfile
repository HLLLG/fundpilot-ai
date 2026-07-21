# Lighthouse 生产部署从仓库根目录读取 Dockerfile（monorepo 入口）
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
COPY apps/api/scripts/evaluate_decision_quality.py /app/scripts/evaluate_decision_quality.py

# 因子 IC 离线回测产物由 scripts/run_factor_ic.py 生成，供
# factor_confidence.py::load_ic_summary 读取。`.gitkeep` 保证干净 checkout 中目录存在；
# summary.json 缺失时服务会诚实降级为「证据不足」，不会阻断镜像构建。
COPY apps/api/var/factor_ic /app/var/factor_ic

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV FUND_AI_OCR_PRELOAD=false
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
# Uvicorn reads WEB_CONCURRENCY when --workers is omitted. Two workers are the
# safe default for the 4-core Lighthouse host because each worker also owns
# bounded OCR, market-data and analysis thread pools.
ENV WEB_CONCURRENCY=2

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
