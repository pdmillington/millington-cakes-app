# holded_api.py
# =============================================================================
# All Holded API calls. Mirrors the pattern of millington_db.py —
# no other file talks to the Holded API directly.
#
# Caching strategy (two layers):
#   1. Supabase holded_year_cache — persistent across sessions
#      Historical years written once; current year always refreshed.
#      Cache is versioned: bumping _CACHE_VERSION forces a full re-fetch
#      of all years (needed when _DOC_TYPES changes).
#   2. st.session_state — in-memory within a browser session (30-min TTL)
#
# Document types fetched:
#   invoice      = Factura         (wholesale orders)
#   salesreceipt = Ticket de venta (Shopify / retail orders)
# =============================================================================

import os
import time
import requests
import streamlit as st
from datetime import datetime, timezone
from dotenv import load_dotenv

import millington_db as db

load_dotenv()

_BASE_URL      = "https://api.holded.com/api/invoicing/v1"
_SESSION_TTL   = 30 * 60    # 30 min session-state TTL
_DATA_START    = 2023       # earliest year with Holded data
_CACHE_VERSION = 3          # bump when _DOC_TYPES changes → forces full re-fetch

# All Holded document types that count as sales revenue
_DOC_TYPES = ["invoice", "salesreceipt", "creditnote"]


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


def _fetch_doc_type_year(doc_type: str, year: int) -> list[dict]:
    """Fetch all pages of a single doc type for a single year."""
    start_ts, end_ts = _year_bounds(year)
    results: dict[str, dict] = {}
    page = 1

    while True:
        r = requests.get(
            f"{_BASE_URL}/documents/{doc_type}",
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

        for doc in batch:
            if not doc.get("draft"):
                doc["_doc_type"] = doc_type   # tag so callers can distinguish
                results[doc["id"]] = doc

        page += 1

    return list(results.values())


def _fetch_year_from_holded(year: int) -> list[dict]:
    """
    Fetch all doc types for a given year and merge by id.
    Each document is tagged with _doc_type ('invoice' or 'salesreceipt').
    """
    all_docs: dict[str, dict] = {}
    for doc_type in _DOC_TYPES:
        for doc in _fetch_doc_type_year(doc_type, year):
            all_docs[doc["id"]] = doc
    return list(all_docs.values())


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
        data  = r.json()
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
    Return all non-draft sales documents (invoices + salesreceipts)
    from _DATA_START to today.

    Load order per year:
      1. st.session_state  — fastest, within-session cache
      2. Supabase cache    — persistent, historical years only
                             invalidated if cache_version < _CACHE_VERSION
      3. Holded API        — always used for current year; fallback otherwise
    """
    session_key = "_holded_invoices"
    ts_key      = "_holded_invoices_ts"
    now         = time.time()

    # Fast path: valid session cache
    if (
        not force_refresh
        and session_key in st.session_state
        and (now - st.session_state.get(ts_key, 0)) < _SESSION_TTL
    ):
        return st.session_state[session_key]

    current_year = datetime.now().year
    all_docs: dict[str, dict] = {}

    # Index of what's in Supabase: {year: {cache_version, ...}}
    cached_years = {row["year"]: row for row in db.get_holded_cache_index()}

    years    = list(range(_DATA_START, current_year + 1))
    progress = st.progress(0.0, text="Cargando datos de ventas…")

    for i, year in enumerate(years):
        is_current   = (year == current_year)
        cached       = cached_years.get(year)
        cache_valid  = (
            cached is not None
            and cached.get("cache_version", 1) >= _CACHE_VERSION
        )

        progress.progress(
            i / len(years),
            text=f"{'🔄' if (is_current or not cache_valid) else '✓'} {year}…"
        )

        if is_current or not cache_valid:
            docs_year = _fetch_year_from_holded(year)
            if not is_current:
                db.save_holded_year_cache(year, docs_year, _CACHE_VERSION)
        else:
            docs_year = db.get_holded_year_cache(year)

        for doc in docs_year:
            all_docs[doc["id"]] = doc

    progress.progress(1.0, text=f"✓ {len(all_docs)} documentos cargados")
    time.sleep(0.4)
    progress.empty()

    docs = sorted(all_docs.values(), key=lambda x: x.get("date", 0))

    st.session_state[session_key] = docs
    st.session_state[ts_key]      = now
    return docs


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
    ts = st.session_state.get("_holded_invoices_ts")
    if not ts:
        return None
    secs = int(time.time() - ts)
    if secs < 60:   return "just now"
    if secs < 3600: return f"{secs // 60} min ago"
    return f"{secs // 3600}h ago"


def cache_status() -> list[dict]:
    return db.get_holded_cache_index()


# =============================================================================
# Public — line items
# =============================================================================

def get_line_items(invoices: list[dict]) -> list[dict]:
    """
    Flatten all documents into one row per line item.

    Extra field vs original:
      doc_type  — 'invoice (Factura), salesreceipt (Ticket de venta), creditnote (Venta rectificativa))
    """
    rows = []
    for inv in invoices:
        date         = inv.get("date", 0)
        contact_id   = inv.get("contact", "")
        contact_name = (inv.get("contactName") or "").strip()
        doc_number   = inv.get("docNumber") or inv.get("id", "")
        doc_type     = inv.get("_doc_type", "invoice")

        is_credit = (doc_type == "creditnote")
        # Credit notes link back to the invoice they correct
        corrects   = (inv.get("from") or {})
        corrects_id = corrects.get("id", "") if isinstance(corrects, dict) else ""

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
            # Negate line revenue for credit notes so they reduce totals
            sign     = -1 if is_credit else 1
            line_rev = sign * price * units * (1 - discount / 100)

            rows.append({
                "invoice_id":        inv.get("id", ""),
                "doc_number":        doc_number,
                "doc_type":          doc_type,
                "contact_id":        contact_id,
                "contact_name":      contact_name,
                "date":              date,
                "name":              (line.get("name") or "").strip(),
                "sku":               sku,
                "price":             price,
                "units":             units,
                "discount":          discount,
                "tax":               tax,
                "line_revenue":      line_rev,
                "is_product":        bool(sku),
                "corrects_invoice":  corrects_id,
            })

    return rows


# =============================================================================
# Credit note sign handling
# =============================================================================
# Holded stores creditnote subtotals as positive numbers.
# Call this to get the correctly-signed subtotal for revenue calculations.

def signed_subtotal(doc: dict) -> float:
    """
    Return the ex-VAT revenue contribution of a document.

    Holded stores all credit note fields as positive numbers — the docType
    implies the deduction. We negate the subtotal so credit notes correctly
    reduce monthly revenue totals. The `from` field on credit notes links
    back to the original invoice being corrected (visible in audit tab).
    """
    subtotal = float(doc.get("subtotal") or 0)
    if doc.get("_doc_type") == "creditnote":
        return -subtotal
    return subtotal
