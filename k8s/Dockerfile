FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY src /app/src
USER 10001:10001
ENTRYPOINT ["python", "-m", "gridpulse.gridflex.operator"]
