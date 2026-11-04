# The production image: one per commit, built and smoke-tested in CI
# (deploy/smoke.sh) and tagged with the commit it was built from.

FROM node:24-slim AS css
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY static static
COPY templates templates
RUN npm run css

FROM python:3.14-slim-trixie AS python
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.14-slim-trixie
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod
# The code stays root's and read-only to the process; it writes only uploads and
# the static files it hands to nginx.
RUN useradd --system --uid 10001 --home-dir /app premshop \
    && mkdir -p /app/media /srv/static \
    && chown premshop:premshop /app/media /srv/static
COPY --from=python /app/.venv /app/.venv
COPY . .
COPY --from=css /build/static/css/app.css static/css/app.css
# prod.py refuses to start without its secrets. These placeholders live only in
# this command's environment, and collectstatic opens no connection with them.
# input.css is Tailwind's source, which no page loads: its @import names an npm
# package that manifest storage cannot hash.
RUN SECRET_KEY=collectstatic ALLOWED_HOSTS=localhost SITE_URL=https://localhost \
    DATABASE_URL=postgres://collectstatic@localhost/collectstatic \
    EMAIL_HOST_USER=collectstatic EMAIL_HOST_PASSWORD=collectstatic ADMIN_PATH=collectstatic/ \
    FIELD_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
    python manage.py collectstatic --noinput --verbosity 0 --ignore input.css
USER premshop
ARG GIT_COMMIT=""
ENV GIT_COMMIT=$GIT_COMMIT
EXPOSE 8000
# No control socket: nothing here uses gunicornc, and the process cannot write one.
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--no-control-socket"]
