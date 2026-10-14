FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project
COPY bot.py .
RUN mkdir -p /app/data
ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "bot.py"]
