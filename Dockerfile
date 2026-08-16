# Lighthouse 生产部署从仓库根目录读取 Dockerfile（monorepo 入口）
FROM python:3.12-slim

WORKDIR /app

# 截图识别只走云端 qwen-vl-ocr，镜像不再安装本地 PaddleOCR
# （省掉约 550 MiB 依赖和 libgl1/libgomp1 等系统库）。
COPY apps/api/requirements.txt /app/
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r /app/requirements.txt

COPY apps/api/app /app/app
# 每交易日的板块方向状态捕获（.github/workflows/sector-direction-capture.yml 通过
# `docker compose exec api` 调用）。**这份根目录 Dockerfile 才是生产用的那份**
# （docker-compose.production.yml 里 `context: .` + `dockerfile: Dockerfile`），
# apps/api/Dockerfile 是另一条路径。镜像逐个白名单拷贝脚本，漏了这行定时任务会
# 直接报「No such file or directory」——已经实测踩过一次。
COPY apps/api/scripts/capture_sector_direction_states.py /app/scripts/capture_sector_direction_states.py

# 因子 IC 离线回测产物由 scripts/run_factor_ic.py 生成，供
# factor_confidence.py::load_ic_summary 读取。`.gitkeep` 保证干净 checkout 中目录存在；
# summary.json 缺失时服务会诚实降级为「证据不足」，不会阻断镜像构建。
COPY apps/api/var/factor_ic /app/var/factor_ic

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
# Uvicorn reads WEB_CONCURRENCY when --workers is omitted. Two workers are the
# safe default for the 4-core Lighthouse host because each worker also owns
# bounded market-data and analysis thread pools.
ENV WEB_CONCURRENCY=2

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
