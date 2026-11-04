#!/usr/bin/env bash
# Rehearses scripts/deploy.py on the production stack with throwaway secrets, the
# way the runbooks in docs/runbooks/ use it: a first deploy; a deploy of an image
# that never answers, which must end back on the first image by itself; a deploy
# refused because its compose file changes db and redis; a deploy carrying a
# changed nginx file, which must take effect, and a rollback; and restores into
# fresh volumes: the last backup must decrypt the seeded input with the right key
# and fail with a different one, and the first, taken before any order, must fail
# without --allow-empty. CI runs it after deploy/smoke.sh.
#
#   bash deploy/rehearse.sh premshop:<commit> <commit>
set -euo pipefail

image=$1
commit=$2
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
# The server's checkout, copied so the rehearsal can change its files.
checkout=$work/checkout
mkdir -p "$checkout/scripts"
cp -R "$here" "$checkout/deploy"
cp "$here/../scripts/deploy.py" "$checkout/scripts/"
broken=premshop:rehearsal-broken
second=premshop:rehearsal-second
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-premshop-rehearsal} PREMSHOP_IMAGE=$image
export PREMSHOP_HTTPS_PORT=${PREMSHOP_HTTPS_PORT:-8543} PREMSHOP_HTTP_PORT=${PREMSHOP_HTTP_PORT:-8180}
export PREMSHOP_ENV_FILE=$work/premshop.env PREMSHOP_TLS_DIR=$work/tls
export PREMSHOP_STATE_FILE=$work/state/deploy.json PREMSHOP_BACKUP_DIR=$work/backups
export PREMSHOP_HEALTH_URL=https://localhost:$PREMSHOP_HTTPS_PORT/healthz
# The health check verifies TLS against the certificate made below for this run.
export SSL_CERT_FILE=$work/tls/fullchain.pem

stack() {
    docker compose --env-file "$PREMSHOP_ENV_FILE" --file "$checkout/deploy/compose.yaml" "$@"
}
deploy() {
    python3 "$checkout/scripts/deploy.py" "$@" --health-timeout 60
}
fail() {
    echo "rehearse: $*" >&2
    exit 1
}
finish() {
    status=$?
    [ "$status" -eq 0 ] || stack logs --no-color --tail 60 || true
    stack down --volumes --remove-orphans >/dev/null 2>&1 || true
    docker image rm "$broken" "$second" >/dev/null 2>&1 || true
    rm -rf "$work"
    exit "$status"
}
trap finish EXIT
serving() {
    curl -s --cacert "$SSL_CERT_FILE" "$PREMSHOP_HEALTH_URL" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["commit"])' 2>/dev/null || true
}
on() {
    [ "$(serving)" = "${1#premshop:}" ] || fail "/healthz does not report ${1#premshop:} $2"
    [ "$(stack ps --format '{{.Image}}' web worker | sort -u)" = "$1" ] \
        || fail "web and worker are not both on $1 $2"
    grep -q "\"current\": \"$1\"" "$PREMSHOP_STATE_FILE" || fail "the state file does not record $1 $2"
}

mkdir "$work/tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -addext subjectAltName=DNS:localhost \
    -keyout "$work/tls/privkey.pem" -out "$work/tls/fullchain.pem" 2>/dev/null
secret() { openssl rand -hex 24; }
key() { openssl rand -base64 32 | tr '+/' '-_'; }
umask 077
# EMAIL_HOST is under .invalid, which never resolves (RFC 2606): no message leaves.
cat >"$PREMSHOP_ENV_FILE" <<EOF
SECRET_KEY=$(secret)
ALLOWED_HOSTS=localhost
SITE_URL=https://localhost
CSRF_TRUSTED_ORIGINS=https://localhost
EMAIL_HOST=smtp.invalid
EMAIL_HOST_USER=rehearsal
EMAIL_HOST_PASSWORD=$(secret)
DEFAULT_FROM_EMAIL=support@premshop.ir
FIELD_ENCRYPTION_KEY=$(key)
ADMIN_PATH=rehearsal-panel/
POSTGRES_PASSWORD=$(secret)
REDIS_PASSWORD=$(secret)
EOF
wrong=$work/wrong-key.env
sed "s/^FIELD_ENCRYPTION_KEY=.*/FIELD_ENCRYPTION_KEY=$(key)/" "$PREMSHOP_ENV_FILE" >"$wrong"

# The same code under other tags: one whose web process exits at once, and one
# that answers as a second release would.
docker build --quiet --tag "$broken" - >/dev/null <<EOF
FROM $image
CMD ["python", "-c", "raise SystemExit('this image fails on purpose')"]
EOF
docker build --quiet --tag "$second" - >/dev/null <<EOF
FROM $image
ENV GIT_COMMIT=${second#premshop:}
EOF

deploy deploy "$image" || fail "the first deploy of $image failed"
on "$image" "after the first deploy"

# One order item with encrypted input, placed through the order service.
stack run --rm --no-deps -T web python manage.py shell --command "$(cat <<'EOF'
from decimal import Decimal
from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product
from apps.orders.services import OrderLine, place_order

category = Category.objects.create(name="Rehearsal", slug="rehearsal")
product = Product.objects.create(
    category=category, name="Rehearsal", slug="rehearsal", status=Product.Status.ACTIVE,
    warranty=Product.Warranty.DAYS_7,
)
plan = Plan.objects.create(
    product=product, title="Rehearsal", sale_price=Decimal("200000"),
    cost_price=Decimal("100000"), duration_days=30, max_quantity=1, input_type="email",
    requires_customer_input=True, telegram_required=False,
)
buyer = User.objects.create_user("rehearsal@example.test")
place_order(user=buyer, lines=[OrderLine(plan, 1, "rehearsal-input@example.test")])
EOF
)"

if deploy deploy "$broken"; then
    fail "the deploy of an image that never answers reported success"
fi
on "$image" "after the failed deploy returned by itself"
echo "rehearse: a failed health check returned the stack to $image by itself"

# A deploy never recreates db or redis: a compose file that changes them stops it
# before the backup.
db=$(stack ps --quiet db)
backups=$(ls "$PREMSHOP_BACKUP_DIR")
cp "$checkout/deploy/compose.yaml" "$work/compose.yaml"
sed -i 's/retries: 12/retries: 13/' "$checkout/deploy/compose.yaml"
if deploy deploy "$second" | tee "$work/stores.log"; then
    fail "a deploy whose compose file changes db and redis went ahead"
fi
grep -q "changes the db or redis service" "$work/stores.log" || fail "the refused deploy gave no reason"
[ "$(stack ps --quiet db)" = "$db" ] || fail "the refused deploy recreated the database"
[ "$(ls "$PREMSHOP_BACKUP_DIR")" = "$backups" ] || fail "the refused deploy took a backup"
cp "$work/compose.yaml" "$checkout/deploy/compose.yaml"
on "$image" "after the refused deploy"
echo "rehearse: a compose file that changes db and redis stopped the deploy before anything changed"

# A changed nginx file takes effect with the deploy that brings it, though nginx
# itself is not recreated.
sed -i 's|^    location = /maintenance.html {|    location = /rehearsal { return 200 changed; }\n&|' \
    "$checkout/deploy/nginx/premshop.conf"
deploy deploy "$second" || fail "the deploy of $second failed"
on "$second" "after its deploy"
[ "$(curl -s --cacert "$SSL_CERT_FILE" "https://localhost:$PREMSHOP_HTTPS_PORT/rehearsal")" = changed ] \
    || fail "nginx did not take up the changed file"
echo "rehearse: the deploy reloaded nginx with the changed file"
deploy rollback || fail "rollback failed"
on "$image" "after the rollback"
echo "rehearse: rollback returned the stack to $image"

# The last backup, taken before the second deploy, holds the order.
dump=$PREMSHOP_BACKUP_DIR/$(ls "$PREMSHOP_BACKUP_DIR" | sort | tail -1)
stack down --volumes >/dev/null 2>&1
deploy restore "$dump" "$image" | tee "$work/restore.log" || fail "the restore failed"
grep -q "orders.OrderItem.customer_input: 1 decrypted, 0 failed" "$work/restore.log" \
    || fail "the restore did not decrypt the seeded input"
on "$image" "after the restore"
if grep -q rehearsal-input "$work/restore.log"; then
    fail "the restore printed a decrypted value"
fi

stack down --volumes >/dev/null 2>&1
if deploy restore "$dump" "$image" --env-file "$wrong" | tee "$work/wrong.log"; then
    fail "a restore under a different key reported success"
fi
grep -q "orders.OrderItem.customer_input: 0 decrypted, 1 failed" "$work/wrong.log" \
    || fail "the restore under a different key did not report the value it could not read"
[[ $(stack ps --status running --services) != *web* ]] || fail "web runs after a failed restore"
echo "rehearse: the restore decrypts with the right key and fails, closed, with a different one"

# The first backup was taken before any order, so it holds no value that proves the key.
first=$PREMSHOP_BACKUP_DIR/$(ls "$PREMSHOP_BACKUP_DIR" | sort | head -1)
stack down --volumes >/dev/null 2>&1
if deploy restore "$first" "$image" 2>&1 | tee "$work/empty.log"; then
    fail "a restore of a backup with no encrypted value reported success"
fi
grep -q "nothing proves the key" "$work/empty.log" || fail "the empty restore gave no reason"
[[ $(stack ps --status running --services) != *web* ]] || fail "web runs after a restore that proved no key"
echo "rehearse: a backup with no encrypted value fails the restore without --allow-empty"
