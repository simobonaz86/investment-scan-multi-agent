FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install -U pip && pip install .

RUN useradd -m appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "invest_scan.main:app", "--host", "0.0.0.0", "--port", "8000"]

