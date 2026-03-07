FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.9 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN uv pip install --system -e .

EXPOSE 8090
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
