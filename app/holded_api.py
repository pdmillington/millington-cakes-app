# holded_api.py
# =============================================================================
# All Holded API calls. Mirrors the pattern of millington_db.py —
# no other file talks to the Holded API directly.
#
# Caching strategy (two layers):
#   1. Supabase holded_year_cache table  — persistent across sessions
#      - Historical years (< current year): written once, never re-fetched
#      - Current year: always refreshed from Holded on each sync
#   2. st.session_state                  — in-memory within a browser session
#      - Avoids repeated Supabase reads within the same session
#      - TTL: 30 minutes
#
# On first ever run all years since DATA_START are fetched from Holded
# and stored in Supabase (~10–15 seconds). Every subsequent load fetches
# only the current year from Holded; historical data comes from Supabase.
#
# Public API:
#   get_invoices(force_refresh)   →  list[dict]
#   get_contacts(force_refresh)   →  list[dict]
#   get_line_items(invoices)      →  list[dict]
#   last_synced()                 →  str | None
#   cache_status()                →  list[dict]   one row per cached year
# =============================================================================

import os
import time
import requests
import streamlit as st
from datetime import datetime, timezone
from dotenv import load_dotenv

import millington_db as db

load_dotenv()

_BASE_URL    = "https://api.holded.com/api/invoicing/v1"
_SESSION_TTL = 30 * 60      # 30 min session-state TTL
_DATA_START  = 2023         # earliest year with Holded data


# =============================================================================
# Internal — Holded API
# =============================================================================

def _api_key() -> str:
    try:
        return st.secrets["HOLDED_API_KEY"]
    except Exception:
        return os.getenv("HOLDED_API_KEY", "")


def _headers() -> dict:
    return {"key": _api_key(), "Accept": "application/json"}


def _year_bounds(year: int) -> tuple[int, int]:
    start = int(datetime(year,  1,  1,  0,  0,  0, tzinfo=timezone.utc).timestamp())
    end   = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
    return start, end


def _fetch_year_from_holded(year: int) -> list[dict]:
    """
    Pull all non-draft invoices for a single calendar year from the Holded API,
    paginating until an empty page is returned.
    """
    start_ts, end_ts = _year_bounds(year)
    results: dict[str, dict] = {}   # id → invoice, deduplicates within year
    page = 1

    while True:
        r = requests.get(
            f"{_BASE_URL}/documents/invoice",
            headers=_headers(),
            params={"starttdate": start_ts, "enddate": end_ts, "page": page},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        batch = (
            data if isinstance(data, list)
            else data.get("documents") or data.get("list") or data.get("data") or []
        )
        if not batch:
            break

        for inv in batch:
            if not inv.get("draft"):
                results[inv["id"]] = inv

        page += 1

    return list(results.values())


def _fetch_contacts_from_holded() -> list[dict]:
    """Pull all contacts (all pages) from the Holded API."""
    results = []
    page    = 1
    while True:
        r = requests.get(
            f"{_BASE_URL}/contacts",
            headers=_headers(),
            params={"page": page},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        batch = (
            data if isinstance(data, list)
            else data.get("contacts") or data.get("list") or data.get("data") or []
        )
        if not batch:
            break
        results.extend(batch)
        page += 1
    return results


# =============================================================================
# Public — invoices
# =============================================================================

def get_invoices(force_refresh: bool = False) -> list[dict]:
    """
    Return all non-draft invoices from DATA_START to today.

    Load order for each year:
      1. st.session_state  (fastest — within session)
      2. Supabase cache    (fast — historical years only)
      3. Holded API        (slow — only when not cached or current year)

    force_refresh=True clears session state and re-fetches current year
    from Holded. Historical years are never re-fetched unless the Supabase
    cache row is manually deleted.
    """
    session_key = "_holded_invoices"
    ts_key      = "_holded_invoices_ts"
    now         = time.time()

    # Session-state hit (fast path)
    if (
        not force_refresh
        and session_key in st.session_state
        and (now - st.session_state.get(ts_key, 0)) < _SESSION_TTL
    ):
        return st.session_state[session_key]

    current_year = datetime.now().year
    all_invoices: dict[str, dict] = {}   # id → invoice

    # Load existing Supabase cache so we know which years are already stored
    cached_years = {row["year"]: row for row in db.get_holded_cache_index()}

    years = list(range(_DATA_START, current_year + 1))
    progress = st.progress(0.0, text="Cargando facturas…")

    for i, year in enumerate(years):
        is_current = (year == current_year)
        label      = f"{year} {'(actualizando…)' if is_current else ''}"
        progress.progress(i / len(years), text=f"Cargando {label}")

        if is_current or year not in cached_years:
            # Fetch from Holded
            invoices_year = _fetch_year_from_holded(year)

            if not is_current:
                # Persist historical year to Supabase — will never be fetched again
                db.save_holded_year_cache(year, invoices_year)
        else:
            # Load from Supabase cache
            invoices_year = db.get_holded_year_cache(year)

        for inv in invoices_year:
            all_invoices[inv["id"]] = inv

    progress.progress(1.0, text=f"✓ {len(all_invoices)} facturas cargadas")
    time.sleep(0.4)
    progress.empty()

    invoices = sorted(all_invoices.values(), key=lambda x: x.get("date", 0))

    st.session_state[session_key] = invoices
    st.session_state[ts_key]      = now
    return invoices


def get_contacts(force_refresh: bool = False) -> list[dict]:
    """Return all contacts from Holded, cached in session_state."""
    session_key = "_holded_contacts"
    ts_key      = "_holded_contacts_ts"
    now         = time.time()

    if (
        not force_refresh
        and session_key in st.session_state
        and (now - st.session_state.get(ts_key, 0)) < _SESSION_TTL
    ):
        return st.session_state[session_key]

    with st.spinner("Sincronizando contactos de Holded…"):
        contacts = _fetch_contacts_from_holded()

    st.session_state[session_key] = contacts
    st.session_state[ts_key]      = now
    return contacts


def last_synced() -> str | None:
    """Human-readable age of the current session cache, or None if not loaded."""
    ts = st.session_state.get("_holded_invoices_ts")
    if not ts:
        return None
    secs = int(time.time() - ts)
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    return f"{secs // 3600}h ago"


def cache_status() -> list[dict]:
    """
    Return summary of what's in the Supabase cache.
    Used in the KPI screen to show cache health.
    """
    return db.get_holded_cache_index()


# =============================================================================
# Public — line items
# =============================================================================

def get_line_items(invoices: list[dict]) -> list[dict]:
    """
    Flatten invoices into one row per line item with invoice metadata.

    Fields per row:
      invoice_id, doc_number, contact_id, contact_name, date (Unix ts),
      name, sku (str, "" for non-product lines), price (ex-VAT unit),
      units, discount (%), tax (%), line_revenue (ex-VAT after discount),
      is_product (bool — False for delivery charges etc.)
    """
    rows = []
    for inv in invoices:
        date         = inv.get("date", 0)
        contact_id   = inv.get("contact", "")
        contact_name = (inv.get("contactName") or "").strip()
        doc_number   = inv.get("docNumber") or inv.get("id", "")

        for line in (inv.get("products") or []):
            raw_sku = line.get("sku")
            sku = (
                "" if (raw_sku is None or raw_sku == 0 or raw_sku == "0")
                else str(raw_sku).strip()
            )
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
