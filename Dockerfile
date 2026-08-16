FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# 阿里云pip镜像（国内服务器构建提速，避免PyPI官方源超时）
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

COPY app ./app
COPY config ./config
COPY assets ./assets

EXPOSE 8200

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200", "--log-level", "info"]
