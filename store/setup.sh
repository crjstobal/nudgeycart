#!/usr/bin/env bash
# Builds a throwaway WooCommerce store for testing NudgeyCart, fully local:
# MariaDB with its own data dir inside store/.mariadb, WordPress in store/wp.
# Re-running is safe; `store/reset.sh` wipes everything.
set -euo pipefail
source "$(dirname "$0")/env.sh"
export NUDGEY_BACKEND="$REPO_DIR/backend"

command -v mariadbd >/dev/null || { echo "Install MariaDB first: brew install mariadb" >&2; exit 1; }
command -v wp >/dev/null || { echo "Install WP-CLI first: brew install wp-cli" >&2; exit 1; }

echo "==> Database"
start_db
mariadb --socket="$DB_SOCK" -u"$(whoami)" <<SQL
CREATE DATABASE IF NOT EXISTS nudgeystore;
CREATE USER IF NOT EXISTS 'nudgey'@'127.0.0.1' IDENTIFIED BY 'nudgey';
GRANT ALL ON nudgeystore.* TO 'nudgey'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

echo "==> WordPress"
mkdir -p "$WP_DIR"
[ -f "$WP_DIR/wp-load.php" ] || wpc core download --quiet
if [ ! -f "$WP_DIR/wp-config.php" ]; then
  wpc config create --dbname=nudgeystore --dbuser=nudgey --dbpass=nudgey --dbhost="127.0.0.1:$DB_PORT" --skip-check --quiet
  # 'local' lets Application Passwords work over plain http on localhost.
  wpc config set WP_ENVIRONMENT_TYPE local --quiet
  wpc config set WP_DEBUG false --raw --quiet
fi
if ! wpc core is-installed 2>/dev/null; then
  wpc core install --url="$WP_URL" --title="Nudgey Demo Store" --admin_user=admin \
    --admin_password=admin --admin_email=admin@nudgeycart.test --skip-email --quiet
fi
wpc rewrite structure '/%postname%/' --quiet

echo "==> WooCommerce + theme"
wpc plugin is-installed woocommerce || wpc plugin install woocommerce --quiet
wpc plugin activate woocommerce --quiet
wpc theme is-installed storefront || wpc theme install storefront --quiet
wpc theme activate storefront --quiet
ln -sfn "$REPO_DIR/wordpress-plugin/nudgeycart" "$WP_DIR/wp-content/plugins/nudgeycart"
wpc plugin activate nudgeycart --quiet

CURRENCY="$($PY - <<'PY'
import sys; import os; sys.path.insert(0, os.environ["NUDGEY_BACKEND"])
from nudgeycart.secrets import get_key
print(get_key("paypal-sandbox.env", "PAYPAL_CURRENCY"))
PY
)" || CURRENCY=USD
echo "    currency: $CURRENCY"

wpc option update woocommerce_currency "$CURRENCY" --quiet || true
wpc option update woocommerce_default_country "US:CA" --quiet || true
wpc option update woocommerce_store_address "1 Market St" --quiet || true
wpc option update woocommerce_store_city "San Francisco" --quiet || true
wpc option update woocommerce_store_postcode "94105" --quiet || true
wpc option update woocommerce_calc_taxes no --quiet || true
wpc option update woocommerce_default_customer_address base --quiet || true
wpc option update woocommerce_coming_soon no --quiet || true
wpc option update woocommerce_onboarding_profile '{"skipped":true}' --format=json --quiet || true
wpc option update woocommerce_task_list_hidden yes --quiet || true

echo "==> Shipping + offline payment (so control-group shoppers can still buy normally)"
if ! wpc wc shipping_zone_method list 0 --user=admin --format=ids 2>/dev/null | grep -q .; then
  wpc wc shipping_zone_method create 0 --method_id=flat_rate --enabled=true \
    --settings='{"title":"Flat rate","cost":"7.99"}' --user=admin --quiet
fi
wpc wc payment_gateway update cod --enabled=true --user=admin --quiet

echo "==> Products"
if [ "$(wpc wc product list --user=admin --format=count)" = "0" ]; then
  while IFS='|' read -r name price cost sku img desc; do
    wpc wc product create --user=admin --name="$name" --type=simple --regular_price="$price" --sku="$sku" \
      --description="$desc" --short_description="$desc" \
      --meta_data="[{\"key\":\"nudgey_cost\",\"value\":\"$cost\"}]" \
      --images="[{\"src\":\"https://picsum.photos/seed/$img/800/800.jpg\"}]" --porcelain >/dev/null \
      || wpc wc product create --user=admin --name="$name" --type=simple --regular_price="$price" --sku="$sku" \
           --description="$desc" --meta_data="[{\"key\":\"nudgey_cost\",\"value\":\"$cost\"}]" --porcelain >/dev/null
    echo "    + $name ($price, cost $cost)"
  done <<'PRODUCTS'
Aurora Wireless Headphones|189.00|96.00|NC-AUR-01|headphones|Over-ear noise cancelling headphones with 40h battery.
Trailblazer Hiking Backpack|129.00|58.00|NC-TRL-02|backpack|28L waterproof daypack with hydration sleeve.
Brewmaster Pour-Over Kit|64.00|27.00|NC-BRW-03|coffee|Glass dripper, kettle and 100 filters.
Nimbus Merino Hoodie|98.00|41.00|NC-NIM-04|hoodie|Midweight merino wool, made to last.
Lumen Smart Desk Lamp|79.00|44.00|NC-LUM-05|lamp|Adjustable color temperature, USB-C charging base.
Pocket Field Notebook (3-pack)|18.00|6.00|NC-PFN-06|notebook|Dot grid, recycled paper.
PRODUCTS
fi

echo "==> REST credentials for the NudgeyCart backend"
wpc user application-password delete admin --all --quiet 2>/dev/null || true
APP_PASS="$(wpc user application-password create admin nudgeycart --porcelain)"

echo "==> Register the store as a NudgeyCart merchant"
BUYER_EMAIL="$($PY - <<'PY'
import sys; import os; sys.path.insert(0, os.environ["NUDGEY_BACKEND"])
from nudgeycart.secrets import get_key
print(get_key("paypal-sandbox.env", "PAYPAL_SANDBOX_BUYER_EMAIL"))
PY
)"
CREDS="$(cd "$REPO_DIR/backend" && $PY -m nudgeycart.cli add-merchant --name "Nudgey Demo Store" \
  --store-url "$WP_URL" --woo-user admin --woo-app-password "$APP_PASS" --currency "$CURRENCY" \
  --billing-email "$BUYER_EMAIL" --shipping-flat 7.99 --control-pct 10)"
PUBLIC_KEY="$(echo "$CREDS" | $PY -c 'import json,sys; print(json.load(sys.stdin)["public_key"])')"
ADMIN_TOKEN="$(echo "$CREDS" | $PY -c 'import json,sys; print(json.load(sys.stdin)["admin_token"])')"
wpc option update nudgeycart_public_key "$PUBLIC_KEY" --quiet || true
wpc option update nudgeycart_api_base "$API_URL" --quiet || true
wpc option update nudgeycart_test_mode 1 --quiet || true
echo "$ADMIN_TOKEN" > "$REPO_DIR/backend/data/admin_token.txt"

cat <<EOF

Done.
  Store:      $WP_URL          (wp-admin: admin / admin)
  Dashboard:  $API_URL/dashboard#$ADMIN_TOKEN
  Start all:  ./scripts/dev.sh
EOF
