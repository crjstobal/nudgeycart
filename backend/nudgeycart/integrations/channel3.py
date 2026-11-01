"""Channel3: what the same product costs at other retailers right now.

Used for the price match lever. Lookups are cached per product so the exit-intent
decision never waits on a search: the store warms the cache when an item lands in a cart.
"""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

import httpx

from .. import db, settings
from ..core.policy import MarketOffer, money

log = logging.getLogger("nudgeycart.channel3")

API = "https://api.trychannel3.com/v1/search"
CACHE_TTL_S = 12 * 3600
# Share of the words in our product name that must appear in the listing title.
MIN_TITLE_OVERLAP = 0.6


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1}


def _overlap(ours: str, theirs: str) -> float:
    a = _words(ours)
    return len(a & _words(theirs)) / len(a) if a else 0.0


def _price(offer: dict) -> Decimal | None:
    p = offer.get("price") or {}
    v = p.get("price", p.get("amount"))
    return Decimal(str(v)) if v is not None else None


def search(name: str, currency: str, exclude_domain: str | None = None) -> list[dict]:
    """Comparable new, in-stock listings for `name`, cheapest first."""
    key = settings.channel3_api_key()
    if not key:
        return []
    r = httpx.post(API, headers={"x-api-key": key}, json={"query": name, "limit": 5}, timeout=8)
    r.raise_for_status()
    out = []
    for prod in r.json().get("products", []):
        if _overlap(name, prod.get("title", "")) < MIN_TITLE_OVERLAP:
            continue
        for o in prod.get("offers", []):
            price = _price(o)
            if (price is None or o.get("availability") != "InStock"
                    or o.get("condition") not in (None, "new")
                    or (o.get("price") or {}).get("currency", currency) != currency
                    or (exclude_domain and exclude_domain in (o.get("domain") or ""))):
                continue
            out.append({"title": prod.get("title"), "domain": o.get("domain"), "price": str(money(price)),
                        "url": o.get("url")})
    return sorted(out, key=lambda x: Decimal(x["price"]))


def _cache_key(name: str, currency: str) -> str:
    # Shared across merchants: the same product costs the same elsewhere whoever asks.
    return f"{currency}:{' '.join(sorted(_words(name)))}"


def warm(lines: list[dict], currency: str, exclude_domain: str | None = None) -> int:
    """Fetch and cache listings for products not looked up recently. Returns lookups made."""
    made = 0
    for l in lines[:10]:
        k = _cache_key(l["name"], currency)
        with db.conn() as c:
            hit = c.execute("SELECT fetched_at FROM market_prices WHERE key = ?", (k,)).fetchone()
        if hit and db.now() - hit["fetched_at"] < CACHE_TTL_S:
            continue
        try:
            listings = search(l["name"], currency, exclude_domain)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("channel3 lookup failed for %r: %s", l["name"], e)
            continue
        made += 1
        with db.conn() as c:
            c.execute("INSERT OR REPLACE INTO market_prices (key, data, fetched_at) VALUES (?,?,?)",
                      (k, json.dumps(listings), db.now()))
    return made


def cached_offers(lines: list[dict], currency: str) -> list[MarketOffer]:
    """Cheapest cached listing per product, without making any network call."""
    out = []
    with db.conn() as c:
        for l in lines:
            r = c.execute("SELECT data FROM market_prices WHERE key = ?",
                          (_cache_key(l["name"], currency),)).fetchone()
            listings = json.loads(r["data"]) if r else []
            if listings:
                best = listings[0]
                out.append(MarketOffer(product_id=int(l["product_id"]), domain=best["domain"],
                                       price=Decimal(best["price"]), url=best.get("url")))
    return out
