# The lab image: Python 3.12, the lab, Relate's checked binary, PyTorch on CPU, and the built app (page 07).
FROM node:22-bookworm-slim AS app
WORKDIR /app
COPY app/package.json app/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY app/ ./
RUN npm run build

# Debian 13: the relate-linux-amd64 1.0.1 binary needs glibc 2.39 or newer, and bookworm has 2.36.
FROM python:3.12-slim-trixie AS lab
ENV DO_NOT_TRACK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv
ARG RELATE_VERSION=1.0.1
ARG RELATE_SHA256=2ca69bd6440d25b78c1677d11c0e3f2f5722601a4984475b331abc0a4f9fed2a
ADD https://github.com/relatedb/relate/releases/download/v${RELATE_VERSION}/relate-linux-amd64 /usr/local/bin/relate
RUN echo "${RELATE_SHA256}  /usr/local/bin/relate" | sha256sum -c - && chmod 0755 /usr/local/bin/relate \
    && relate --version
# relate --version above fails the build, not the first pull, when the binary cannot run on this base
WORKDIR /srv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY lab/ lab/
COPY generator/ generator/
COPY factors/ factors/
COPY examples/ examples/
RUN uv sync --frozen --no-dev
COPY --from=app /app/dist /srv/app/dist
ENV PATH="/srv/.venv/bin:${PATH}" LAB_DATA_DIR=/data LAB_APP_DIST=/srv/app/dist LAB_RELATE_HOST=0.0.0.0 \
    LAB_FACTORS_FILE=/srv/factors/factors.yaml LAB_POLICY_FILE=/srv/factors/policy.yaml
RUN useradd --uid 1000 --create-home lab && mkdir -p /data /import && chown lab /data /import
USER 1000
EXPOSE 8000 7687 7474 7475
CMD ["python", "-m", "lab", "serve"]
