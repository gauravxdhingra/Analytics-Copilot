# One image for the BFF (which embeds the orchestrator, gateway and tool servers), the approval
# service and the bootstrap job. Build context is the repository root.
# [VERIFY] pin the base images by digest at each release.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . .
# Editable installs keep the policy and catalogue files where the code expects them.
RUN uv sync --frozen --no-dev --all-packages \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app

USER app
