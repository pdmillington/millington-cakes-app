# shopify_api.py
# =============================================================================
# Shopify Admin API — retail order fetching.
#
# Fetches open (unfulfilled) orders from Shopify and normalises them into
# a common order dict used by screen_orders.py.
#
# Public API:
#   get_open_orders(force_refresh) → list[dict]
#   last_synced()                  → str | None
# =============================================================================

import os
import time
import requests
import streamlit as st
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_API_VERSION = "2024-01"
_SESSION_TTL = 5 * 60   # 5 minutes


def _store() -> str:
    try:
        return st.secrets["SHOPIFY_STORE"]
    except Exception:
        return os.getenv("SHOPIFY_STORE", "")


def _token() -> str:
    try:
        return st.secrets["SHOPIFY_TOKEN"]
    except Exception:
        return os.getenv("SHOPIFY_TOKEN", "")


def _headers() -> dict:
    return {
        "X-Shopify-Access-Token": _token(),
        "Content-Type": "application/json",
    }


def _base() -> str:
    return f"https://{_store()}/admin/api/{_API_VERSION}"


def _fetch_open_orders() -> list[dict]:
    """Fetch all open (unfulfilled) orders from Shopify, all pages."""
    orders = []
    url    = f"{_base()}/orders.json"
    params = {
        "status":              "open",
        "fulfillment_status":  "unfulfilled",
        "limit":               250,
        "fields": (
            "id,name,created_at,customer,"
            "line_items,note,fulfillment_status"
        ),
    }
    while url:
        r = requests.get(url, headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        data    = r.json()
        orders += data.get("orders", [])
        # Pagination via Link header
        link   = r.headers.get("Link", "")
        url    = None
        params = {}
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    break
    return orders


def _normalise(order: dict) -> dict:
    """Convert a Shopify order into the common order dict format."""
    customer = order.get("customer") or {}
    name     = " ".join(filter(None, [
        customer.get("first_name", ""),
        customer.get("last_name", ""),
    ])).strip() or order.get("email", "Unknown")

    lines = []
    for item in order.get("line_items", []):
        lines.append({
            "name":     item.get("title", ""),
            "variant":  item.get("variant_title") or "",
            "sku":      item.get("sku") or "",
            "quantity": float(item.get("quantity") or 0),
        })

    # Delivery date: Shopify has no native field — use note if populated,
    # otherwise fall back to created_at date.
    created = order.get("created_at", "")
    try:
        order_date = datetime.fromisoformat(created).date()
    except Exception:
        order_date = None

    return {
        "source":     "Shopify",
        "ref":        order.get("name", ""),
        "client":     name,
        "order_date": order_date,
        "due_date":   order_date,   # no native delivery date in Shopify
        "lines":      lines,
        "note":       order.get("note") or "",
    }


def get_open_orders(force_refresh: bool = False) -> list[dict]:
    """
    Return open unfulfilled Shopify orders, normalised.
    Cached in session state for _SESSION_TTL seconds.
    """
    cache_key = "_shopify_orders"
    ts_key    = "_shopify_orders_ts"
    now       = time.time()

    if (
        not force_refresh
        and cache_key in st.session_state
        and (now - st.session_state.get(ts_key, 0)) < _SESSION_TTL
    ):
        return st.session_state[cache_key]

    try:
        raw    = _fetch_open_orders()
        orders = [_normalise(o) for o in raw]
    except Exception as e:
        st.session_state[cache_key] = []
        st.session_state[ts_key]    = now
        st.session_state["_shopify_error"] = str(e)
        return []

    st.session_state.pop("_shopify_error", None)
    st.session_state[cache_key] = orders
    st.session_state[ts_key]    = now
    return orders


def last_synced() -> str | None:
    ts = st.session_state.get("_shopify_orders_ts")
    if not ts:
        return None
    secs = int(time.time() - ts)
    if secs < 60:   return "just now"
    if secs < 3600: return f"{secs // 60} min ago"
    return f"{secs // 3600}h ago"
