FROM python:3.12-slim

WORKDIR /app

# Install onchainos CLI
RUN apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -sSL https://raw.githubusercontent.com/okx/onchainos-skills/main/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || \
    pip install --no-cache-dir \
        fastapi uvicorn[standard] httpx pydantic pydantic-settings python-dotenv \
        openai anthropic gql aiohttp eth-account structlog rich typer

COPY src/ ./src/
COPY tests/ ./tests/

EXPOSE 8000

CMD ["python", "-m", "src.main"]
