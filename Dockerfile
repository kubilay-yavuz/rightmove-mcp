# syntax=docker/dockerfile:1.7
# Smithery-friendly container image for rightmove-mcp.
#
# Dependencies are vendored under ./vendor/ by
# scripts/build_for_smithery.py so the image builds without any
# uk-property-* package on PyPI. The block between the
# "BEGIN vendor-install" / "END vendor-install" markers is rewritten
# by that script on every run — edit the script (or PACKAGE_SOURCES
# inside it), not the markers, to change what gets installed.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --upgrade pip

# BEGIN vendor-install
RUN pip install --no-cache-dir \
        ./vendor/uk-property-scrapers \
        ./vendor/uk-property-listings \
        ./vendor/uk-property-apify-client \
        ./vendor/uk-property-apis \
        ./vendor/uk-property-apify-shared[crawler] \
        ".[mcp]"
# END vendor-install

# Playwright Chromium for action-tool FormSubmitter. ``--with-deps`` adds the
# shared libs Chromium needs on Debian. Set ``SKIP_PLAYWRIGHT_INSTALL=1`` at
# build time to skip this step (saves ~400 MB; action tools fail at runtime).
ARG SKIP_PLAYWRIGHT_INSTALL=0
RUN if [ "$SKIP_PLAYWRIGHT_INSTALL" = "0" ]; then \
        playwright install --with-deps chromium ; \
    fi

# SQLite snapshot store lives under ``UK_PROPERTY_DELTA_STORE_PATH`` (read by
# ``uk_property_apify_shared.delta.mcp.default_store_path``). Ephemeral by
# default; callers can override via smithery config or a mounted volume.
ENV UK_PROPERTY_DELTA_STORE_PATH=/tmp/uk-property-mcp/rightmove.sqlite

# Smithery deprecated stdio for hosted servers on 2025-09-07; the container
# now serves MCP over Streamable HTTP on port 8081 (overridable via $PORT).
# The ``rightmove-mcp-http`` console script in pyproject.toml maps to
# ``rightmove_mcp.server:run_http`` which configures FastMCP for HTTP. Local
# stdio users keep the ``rightmove-mcp`` entrypoint via ``pip install``.
ENV HOST=0.0.0.0 \
    PORT=8081
EXPOSE 8081
ENTRYPOINT ["rightmove-mcp-http"]
