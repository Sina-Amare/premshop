#!/usr/bin/env bash
# Runs the production stack from a built image with throwaway secrets and checks
# it through nginx over HTTPS, the way the server will run it. CI runs it on
# every code change; docs/development.md runs it locally.
#
#   bash deploy/smoke.sh premshop:<commit> <commit>
set -euo pipefail

image=$1
commit=$2
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
url="https://localhost:${PREMSHOP_HTTPS_PORT:=8443}"
export PREMSHOP_IMAGE=$image PREMSHOP_HTTPS_PORT PREMSHOP_HTTP_PORT=${PREMSHOP_HTTP_PORT:-8080}
export PREMSHOP_ENV_FILE=$work/premshop.env PREMSHOP_TLS_DIR=$work/tls

stack() {
    docker compose --project-name premshop-smoke --env-file "$PREMSHOP_ENV_FILE" \
        --file "$here/compose.yaml" "$@"
}
fail() {
    echo "smoke: $*" >&2
    exit 1
}
finish() {
    status=$?
    [ "$status" -eq 0 ] || stack logs --no-color --tail 60 || true
    stack down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$work"
    exit "$status"
}
trap finish EXIT

mkdir "$work/tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -keyout "$work/tls/privkey.pem" -out "$work/tls/fullchain.pem" 2>/dev/null
secret() { openssl rand -hex 24; }
umask 077
# Every value production refuses to start without. EMAIL_HOST is under .invalid,
# which never resolves (RFC 2606), so no message can leave the stack.
cat >"$PREMSHOP_ENV_FILE" <<EOF
SECRET_KEY=$(secret)
ALLOWED_HOSTS=localhost
SITE_URL=https://localhost
CSRF_TRUSTED_ORIGINS=https://localhost
EMAIL_HOST=smtp.invalid
EMAIL_HOST_USER=smoke
EMAIL_HOST_PASSWORD=$(secret)
DEFAULT_FROM_EMAIL=support@premshop.ir
FIELD_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
POSTGRES_PASSWORD=$(secret)
REDIS_PASSWORD=$(secret)
EOF

leaked=$(docker run --rm --user root --entrypoint find "$image" / -xdev \( -name .env \
    -o -name '.env.*' -o -path /app/docs-local -o -path /app/backups -o -path /app/.git \
    -o -path '/app/media/*' \) -print)
[ -z "$leaked" ] || fail "private files in the image: $leaked"

stack up --detach --wait db redis
stack run --rm --no-deps web python manage.py migrate --noinput >/dev/null
# seed_catalog seeds only a development database; this one is thrown away at the end.
stack run --rm --no-deps -e DJANGO_SETTINGS_MODULE=config.settings.dev -e DEBUG=true \
    web python manage.py seed_catalog --no-images >/dev/null
stack up --detach web worker nginx

for _ in $(seq 60); do
    curl -skf "$url/healthz" >/dev/null && break
    sleep 1
done
python3 - "$(curl -sk "$url/healthz")" "$commit" <<'EOF' || fail "/healthz does not report $commit"
import json, sys
health = json.loads(sys.argv[1])
assert health["status"] == "ok" and health["commit"] == sys.argv[2], health
EOF

fetch() {
    curl -sk -o "$work/page" -w '%{http_code}' "$url$1"
}
page() {
    [ "$(fetch "$1")" = 200 ] || fail "$1 did not answer 200"
    grep -Eq '/static/css/app\.[0-9a-f]{12}\.css' "$work/page" || fail "$1 has no hashed stylesheet"
}
page /
category=$(grep -Eo 'href="/c/[^"]+/"' "$work/page" | head -1 | cut -d'"' -f2)
page "$category"
product=$(grep -Eo 'href="/p/[^"]+/"' "$work/page" | head -1 | cut -d'"' -f2)
page "$product"
echo "smoke: home, $category and $product answer 200 with a hashed stylesheet"

css=$(grep -Eo '/static/css/app\.[0-9a-f]{12}\.css' "$work/page" | head -1)
headers=$(curl -sk -D - -o /dev/null -H 'Accept-Encoding: gzip' "$url$css")
grep -qi '^cache-control: .*immutable' <<<"$headers" || fail "$css is not cached as immutable"
grep -qi '^content-encoding: gzip' <<<"$headers" || fail "$css is not compressed"

[ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PREMSHOP_HTTP_PORT/")" = 301 ] \
    || fail "plain HTTP is not redirected"

reply=$(stack exec -T redis redis-cli ping 2>&1 || true)
[[ $reply == *NOAUTH* ]] || fail "Redis answered without its password: $reply"
[[ $(stack ps --status running --services) == *worker* ]] || fail "the worker is not running"

# A token over HTTP, over HTTPS to Django, and over HTTPS while Django is down.
token=/track/smoke-private-token/
curl -s -o /dev/null "http://localhost:$PREMSHOP_HTTP_PORT$token"
fetch "$token" >/dev/null
stack stop web >/dev/null
[ "$(fetch /)" = 503 ] || fail "with web stopped, / did not answer 503"
heading=$(sed -n 's:.*<h1>\(.*\)</h1>.*:\1:p' "$here/nginx/maintenance.html")
grep -qF "$heading" "$work/page" || fail "the maintenance page lacks its heading"
fetch "$token" >/dev/null
logs=$(stack logs --no-color nginx web)
[[ $logs == *"/track/"* && $logs != *smoke-private-token* ]] || fail "a tracking token reached a log"

echo "smoke: all checks passed for $commit"
