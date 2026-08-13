FROM python:3.11-slim

# Copy the official uv binary from Astral
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Enable bytecode compilation for faster container startup
ENV UV_COMPILE_BYTECODE=1

# 1. Copy dependency specification files first
COPY pyproject.toml uv.lock ./

# 2. Install dependencies without the project itself (creates a cached layer)
RUN uv sync --frozen --no-dev --no-install-project

# 3. Copy the rest of the application source code
COPY . .

# 4. Run sync again to install the project package itself
RUN uv sync --frozen --no-dev

# 5. Place the virtual environment's binaries into the PATH
ENV PATH="/app/.venv/bin:$PATH"

# Expose the FastAPI port
EXPOSE 8000

# Run Uvicorn server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]