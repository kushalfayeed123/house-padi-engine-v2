FROM python:3.11-slim

# Copy the uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency management files first (to leverage Docker caching)
COPY pyproject.toml uv.lock ./

# Install dependencies using uv (--frozen ensures it uses uv.lock, --system installs to system python)
RUN uv sync --frozen --no-dev --system

# Copy the rest of the application source code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]