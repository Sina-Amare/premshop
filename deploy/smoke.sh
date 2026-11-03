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
ADMIN_PATH=smoke-panel/
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

[ "$(fetch /admin/)" = 404 ] || fail "the panel still answers at /admin/"
[ "$(fetch /smoke-panel/login/)" = 200 ] || fail "the panel does not answer at ADMIN_PATH"

# Headers a visitor writes buy no fresh search budget: nginx overwrites X-Real-IP,
# and X-Forwarded-For is never read (apps/core/ratelimit.py).
limit=$(stack exec -T web python -c \
    'from apps.core.ratelimit import SEARCH_PER_IP; print(SEARCH_PER_IP.times)')
search() {
    curl -sk -o /dev/null -w '%{http_code}' -H "X-Forwarded-For: 198.51.100.$1" \
        -H "X-Real-IP: 192.0.2.$1" "$url/search/?q=smoke"
}
for n in $(seq "$limit"); do
    [ "$(search "$n")" = 200 ] || fail "search $n of $limit was refused"
done
[ "$(search 0)" = 429 ] || fail "forged X-Forwarded-For and X-Real-IP headers lifted the search limit"
# Another address still searches, so the limit counts the visitor nginx saw, not nginx.
other=$(stack exec -T web python - <<'EOF'
import ssl, urllib.error, urllib.request
request = urllib.request.Request("https://nginx/search/?q=smoke", headers={"Host": "localhost"})
try:
    # The certificate made above for this run alone, unchecked as curl -k leaves it.
    print(urllib.request.urlopen(request, context=ssl._create_unverified_context()).status)
except urllib.error.HTTPError as error:
    print(error.code)
EOF
)
[ "$other" = 200 ] || fail "a second address was refused search ($other): the limit counts nginx"
echo "smoke: forged client headers leave the search limit at $limit a minute per visitor"

[ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PREMSHOP_HTTP_PORT/")" = 301 ] \
    || fail "plain HTTP is not redirected"

reply=$(stack exec -T redis redis-cli ping 2>&1 || true)
[[ $reply == *NOAUTH* ]] || fail "Redis answered without its password: $reply"
[[ $(stack ps --status running --services) == *worker* ]] || fail "the worker is not running"
# A crash-looping worker is "running" between its restarts, and one that cannot
# reach Redis may stay up and only log the error.
worker=$(stack ps --all --quiet worker)
[ "$(docker inspect --format '{{.RestartCount}}' "$worker")" = 0 ] || fail "the worker restarted"
worker_logs=$(stack logs --no-color worker)
[[ $worker_logs != *Traceback* ]] || fail "the worker logged a traceback"

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
