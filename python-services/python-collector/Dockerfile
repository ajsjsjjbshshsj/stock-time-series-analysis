FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制服务代码
COPY app app/

# 创建日志目录
RUN mkdir -p logs

# 入口
ENTRYPOINT ["python", "app/main.py"]
CMD ["--help"]
