# holded_api.py
# =============================================================================
# All Holded API calls. Mirrors the pattern of millington_db.py —
# no other file talks to the Holded API directly.
#
# Data is cached in st.session_state for the session (30-min TTL).
# A manual refresh button in screen_kpis.py can force a reload.
# No writes to Supabase — Holded is the source of truth for sales data.
#
# Public API:
#   get_invoices(force_refresh)   →  list[dict]   all invoices, all time
#   get_contacts(force_refresh)   →  list[dict]   all contacts
#   get_line_items(invoices)      →  list[dict]   flattened line items
#   last_synced()                 →  str | None   human-readable cache age
# =============================================================================

import os
import time
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

_BASE_URL  = "https://api.holded.com/api/invoicing/v1"
_CACHE_TTL = 30 * 60   # 30 minutes in seconds


# =============================================================================
# Internal helpers
# =============================================================================

def _api_key() -> str:
    try:
        return st.secrets["HOLDED_API_KEY"]
    except Exception:
        return os.getenv("HOLDED_API_KEY", "")


def _headers() -> dict:
    return {"key": _api_key(), "Accept": "application/json"}


def _get_all_pages(path: str) -> list:
    """
    Paginate through a Holded list endpoint until an empty page is returned.
    Holded ignores date-range params so we always fetch everything and
    filter client-side.
    """
    results = []
    page    = 1
    while True:
        r = requests.get(
            f"{_BASE_URL}/{path}",
            headers=_headers(),
            params={"page": page},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        if isinstance(data, list):
            batch = data
        elif isinstance(data, dict):
            batch = (
                data.get("documents") or data.get("contacts") or
                data.get("list")      or data.get("data") or []
            )
        else:
            break

        if not batch:
            break

        results.extend(batch)
        page += 1

    return results


# =============================================================================
# Public functions
# =============================================================================

def get_invoices(force_refresh: bool = False) -> list[dict]:
    """
    Return all non-draft invoices from Holded, cached in session_state.
    Fetches every page; date filtering is done client-side by callers.
    """
    cache_key = "_holded_invoices"
    ts_key    = "_holded_invoices_ts"

    now = time.time()
    if (
        not force_refresh
        and cache_key in st.session_state
        and (now - st.session_state.get(ts_key, 0)) < _CACHE_TTL
    ):
        return st.session_state[cache_key]

    with st.spinner("Syncing invoices from Holded…"):
        invoices = _get_all_pages("documents/invoice")

    # Drop drafts — they haven't been sent to clients yet
    invoices = [inv for inv in invoices if not inv.get("draft")]

    st.session_state[cache_key] = invoices
    st.session_state[ts_key]    = now
    return invoices


def get_contacts(force_refresh: bool = False) -> list[dict]:
    """Return all contacts from Holded, cached in session_state."""
    cache_key = "_holded_contacts"
    ts_key    = "_holded_contacts_ts"

    now = time.time()
    if (
        not force_refresh
        and cache_key in st.session_state
        and (now - st.session_state.get(ts_key, 0)) < _CACHE_TTL
    ):
        return st.session_state[cache_key]

    with st.spinner("Syncing contacts from Holded…"):
        contacts = _get_all_pages("contacts")

    st.session_state[cache_key] = contacts
    st.session_state[ts_key]    = now
    return contacts


def last_synced() -> str | None:
    """Human-readable time since last successful invoice sync, or None."""
    ts = st.session_state.get("_holded_invoices_ts")
    if not ts:
        return None
    secs = int(time.time() - ts)
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    return f"{secs // 3600}h ago"


def get_line_items(invoices: list[dict]) -> list[dict]:
    """
    Flatten invoices into one row per line item, annotated with invoice
    metadata. Each row contains:

      invoice_id, doc_number, contact_id, contact_name, date (Unix ts),
      name, sku (str, "" if no SKU), price (ex-VAT unit), units, discount,
      tax (%), line_revenue (ex-VAT, after discount), is_product (bool)

    Delivery and non-product lines (Holded sets sku = 0) are included
    with is_product=False so callers can easily exclude them from revenue
    totals if needed (they are small and infrequent).
    """
    rows = []
    for inv in invoices:
        date         = inv.get("date", 0)
        contact_id   = inv.get("contact", "")
        contact_name = (inv.get("contactName") or "").strip()
        doc_number   = inv.get("docNumber") or inv.get("id", "")

        for line in (inv.get("products") or []):
            raw_sku = line.get("sku")
            # Holded uses integer 0 for non-product lines (delivery etc.)
            sku = "" if (raw_sku is None or raw_sku == 0 or raw_sku == "0") \
                     else str(raw_sku).strip()

            price    = float(line.get("price")    or 0)
            units    = float(line.get("units")    or 0)
            discount = float(line.get("discount") or 0)
            tax      = float(line.get("tax")      or 0)
            line_rev = price * units * (1 - discount / 100)

            rows.append({
                "invoice_id":   inv.get("id", ""),
                "doc_number":   doc_number,
                "contact_id":   contact_id,
                "contact_name": contact_name,
                "date":         date,
                "name":         (line.get("name") or "").strip(),
                "sku":          sku,
                "price":        price,
                "units":        units,
                "discount":     discount,
                "tax":          tax,
                "line_revenue": line_rev,
                "is_product":   bool(sku),
            })

    return rows
