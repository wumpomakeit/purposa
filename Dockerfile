FROM python:3.12-slim

WORKDIR /app

# System deps: curl for onchainos installer
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install onchainos CLI
RUN curl -sSL https://raw.githubusercontent.com/okx/onchainos-skills/main/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir \
        fastapi "uvicorn[standard]" httpx pydantic pydantic-settings python-dotenv \
        openai anthropic gql aiohttp eth-account structlog rich typer aiofiles \
 && pip install --no-cache-dir -e . 2>/dev/null || true

# Copy application source
COPY src/ ./src/
COPY static/ ./static/

EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
