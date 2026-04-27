# holded_api.py
# =============================================================================
# Simplified Holded API — current month supplement only.
#
# The primary data source is now Excel exports uploaded via screen_kpis.py
# and stored in Supabase (holded_monthly_revenue, holded_monthly_products).
#
# This module fetches ONLY the current calendar month's documents from the
# Holded API to supplement the uploaded data for the period since the last
# upload. It is called only when the current month has no uploaded data yet.
#
# Document types:
#   invoice      = Factura         (wholesale)
#   salesreceipt = Ticket de venta (Shopify/retail — partial, known limitation)
#   creditnote   = Venta rectificativa (negated)
#
# Public API:
#   get_current_month_supplement(force_refresh) → dict with revenue + products
#   last_synced()                               → str | None
# =============================================================================

import os
import re
import time
import requests
import streamlit as st
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

_BASE_URL    = "https://api.holded.com/api/invoicing/v1"
_SESSION_TTL = 30 * 60
_DOC_TYPES   = ["invoice", "salesreceipt", "creditnote"]

# SKU pattern embedded in product names e.g. "Cookie Box - CO-03-DC-GW"
_SKU_RE = re.compile(r'\b([A-Z]{2}-\d{2}-[A-Z]{2}-[A-Z]{2,4}(?:-[A-Z]{2})?)\b')


def _api_key() -> str:
    try:
        return st.secrets["HOLDED_API_KEY"]
    except Exception:
        return os.getenv("HOLDED_API_KEY", "")


def _headers() -> dict:
    return {"key": _api_key(), "Accept": "application/json"}


def _fetch_all_pages(doc_type: str) -> list[dict]:
    """Fetch all pages of a doc type. Holded ignores date params so we
    fetch everything and filter client-side."""
    results: dict[str, dict] = {}
    page = 1
    while True:
        r = requests.get(
            f"{_BASE_URL}/documents/{doc_type}",
            headers=_headers(),
            params={"page": page},
            timeout=20,
        )
        r.raise_for_status()
        data  = r.json()
        if data is None:
            break
        batch = (
            data if isinstance(data, list)
            else data.get("documents") or data.get("list") or data.get("data") or []
        )
        if not batch:
            break
        for doc in batch:
            if not doc.get("draft"):
                doc["_doc_type"] = doc_type
                results[doc["id"]] = doc
        page += 1
    return list(results.values())


def signed_subtotal(doc: dict) -> float:
    """Ex-VAT revenue contribution — negated for credit notes."""
    subtotal = float(doc.get("subtotal") or 0)
    return -subtotal if doc.get("_doc_type") == "creditnote" else subtotal


def get_current_month_supplement(force_refresh: bool = False) -> dict:
    """
    Fetch current-month documents from Holded API and return:
      {
        "revenue":  float,            # total ex-VAT for current month
        "products": {product_name: units},
        "doc_count": int,
        "note": str,                  # caveat for retail partial data
      }

    Filters API results to current calendar month only.
    Cached in session_state for _SESSION_TTL.
    """
    cache_key = "_holded_supplement"
    ts_key    = "_holded_supplement_ts"
    now       = time.time()

    if (
        not force_refresh
        and cache_key in st.session_state
        and (now - st.session_state.get(ts_key, 0)) < _SESSION_TTL
    ):
        return st.session_state[cache_key]

    today        = datetime.now(tz=timezone.utc)
    current_year = today.year
    current_month= today.month

    revenue  = 0.0
    products: dict[str, float] = {}
    doc_count = 0

    try:
        for doc_type in _DOC_TYPES:
            for doc in _fetch_all_pages(doc_type):
                d = datetime.fromtimestamp(doc.get("date", 0), tz=timezone.utc)
                if d.year != current_year or d.month != current_month:
                    continue

                is_credit = doc_type == "creditnote"
                sign      = -1 if is_credit else 1
                revenue  += signed_subtotal(doc)
                doc_count += 1

                for line in (doc.get("products") or []):
                    raw_sku = line.get("sku")
                    sku_ok  = raw_sku and raw_sku != 0 and str(raw_sku) != "0"
                    if not sku_ok:
                        continue   # skip delivery lines etc.
                    name  = (line.get("name") or "").strip()
                    units = float(line.get("units") or 0) * sign
                    if name:
                        products[name] = products.get(name, 0) + units

        result = {
            "revenue":   revenue,
            "products":  products,
            "doc_count": doc_count,
            "note": (
                "⚠️ Datos de retail (Shopify) pueden ser parciales vía API. "
                "Sube el Excel mensual para datos completos."
            ),
        }
    except Exception as e:
        result = {
            "revenue":   0.0,
            "products":  {},
            "doc_count": 0,
            "note":      f"Error al conectar con Holded: {e}",
        }

    st.session_state[cache_key] = result
    st.session_state[ts_key]    = now
    return result


def last_synced() -> str | None:
    ts = st.session_state.get("_holded_supplement_ts")
    if not ts:
        return None
    secs = int(time.time() - ts)
    if secs < 60:   return "just now"
    if secs < 3600: return f"{secs // 60} min ago"
    return f"{secs // 3600}h ago"
