FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv
FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" GARMINTOKENS=/data/garmin \
    GARMIN_MCP_TRANSPORT=streamable-http GARMIN_MCP_HOST=0.0.0.0 \
    GARMIN_READ_ONLY=true CHATGPT_TOOLSET=wellness AUTH_MODE=oauth
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && \
    groupadd --gid 10001 garmin && useradd --uid 10001 --gid 10001 --home /home/garmin --create-home garmin && \
    mkdir -p /data/garmin && chown 10001:10001 /data/garmin && chmod 700 /data/garmin
COPY scripts/container-entrypoint.py /app/container-entrypoint.py
# Root only prepares the mounted volume. Entrypoint drops to UID/GID 10001 before serving.
ENTRYPOINT ["python", "/app/container-entrypoint.py"]
CMD ["garmin-wellness"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+(os.getenv('PORT') or os.getenv('GARMIN_MCP_PORT') or '8000')+'/healthz',timeout=3)"
