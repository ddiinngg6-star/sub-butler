FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# 配置和状态都挂载进来：/app/config.yaml 和 /app/data
VOLUME ["/app/data"]
ENTRYPOINT ["sub-butler", "-c", "/app/config.yaml"]
CMD ["run"]
