"""Admin CLI: python -m nudgeycart.cli add-merchant --name ... --store-url ..."""
import argparse
import json
import secrets

from . import db


def add_merchant(a) -> None:
    db.init()
    mid = db.new_id("mch")
    m = {
        "id": mid, "name": a.name, "public_key": f"pk_{secrets.token_hex(12)}",
        "admin_token": f"adm_{secrets.token_hex(16)}", "platform": "woocommerce",
        "store_url": a.store_url.rstrip("/"), "woo_user": a.woo_user, "woo_app_password": a.woo_app_password,
        "payee_email": a.payee_email, "billing_email": a.billing_email, "currency": a.currency,
        "min_margin_pct": a.min_margin, "max_discount_pct": a.max_discount, "shipping_flat": a.shipping_flat,
        "fee_pct": a.fee, "control_pct": a.control_pct, "test_mode": int(not a.live), "created_at": db.now(),
    }
    with db.conn() as c:
        # Replace an older merchant for the same store so re-running setup stays idempotent.
        c.execute("DELETE FROM merchants WHERE store_url = ?", (m["store_url"],))
        c.execute(f"INSERT INTO merchants ({','.join(m)}) VALUES ({','.join('?' * len(m))})", tuple(m.values()))
    print(json.dumps({k: m[k] for k in ("id", "public_key", "admin_token")}))


def main() -> None:
    p = argparse.ArgumentParser(prog="nudgeycart")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add-merchant")
    s.add_argument("--name", required=True)
    s.add_argument("--store-url", required=True)
    s.add_argument("--woo-user", required=True)
    s.add_argument("--woo-app-password", required=True)
    s.add_argument("--currency", default="USD")
    s.add_argument("--payee-email")
    s.add_argument("--billing-email")
    s.add_argument("--min-margin", type=float, default=0.25)
    s.add_argument("--max-discount", type=float, default=0.15)
    s.add_argument("--shipping-flat", type=float)
    s.add_argument("--fee", type=float, default=0.05)
    s.add_argument("--control-pct", type=int, default=10)
    s.add_argument("--live", action="store_true", help="disable test mode (no forced groups)")
    s.set_defaults(fn=add_merchant)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
