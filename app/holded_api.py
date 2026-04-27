# holded_api.py
# =============================================================================
# All Holded API calls.
#
# Key design decision: Holded's starttdate/enddate params are unreliable —
# they appear to be ignored and always return the most recent documents.
# We therefore fetch ALL pages with no date filter for each doc type,
# then split by year client-side before caching.
#
# Caching strategy:
#   1. Supabase holded_year_cache — persistent, one row per year
#      Historical years (< current): written once, never re-fetched
#      Current year: always re-fetched from Holded
#      Cache versioned: bump _CACHE_VERSION when _DOC_TYPES changes
#   2. st.session_state — in-memory within a browser session (30-min TTL)
#
# Document types fetched:
#   invoice      = Factura              (wholesale orders)
#   salesreceipt = Ticket de venta      (Shopify / retail orders)
#   creditnote   = Venta rectificativa  (credit notes — stored positive,
#                                        negated when computing revenue)
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


def _fetch_all_pages(doc_type: str) -> list[dict]:
    """
    Fetch every page of a doc type with NO date filtering.

    Holded's date params (starttdate/enddate) are unreliable — they appear
    to be ignored and always return the most recent documents regardless of
    the date range requested. We therefore pull everything and filter by
    year client-side.
    """
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
        data = r.json()

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


def _fetch_all_docs() -> list[dict]:
    """
    Pull all non-draft sales documents across all doc types.
    Returns a flat list sorted by date ascending.
    """
    all_docs: dict[str, dict] = {}
    for doc_type in _DOC_TYPES:
        for doc in _fetch_all_pages(doc_type):
            all_docs[doc["id"]] = doc
    return sorted(all_docs.values(), key=lambda x: x.get("date", 0))


def _split_by_year(docs: list[dict]) -> dict[int, list[dict]]:
    """Group a flat doc list into {year: [docs]} using the date field."""
    by_year: dict[int, list[dict]] = {}
    for doc in docs:
        year = datetime.fromtimestamp(
            doc.get("date", 0), tz=timezone.utc
        ).year
        by_year.setdefault(year, []).append(doc)
    return by_year


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
        if data is None:
            break
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
    Return all non-draft sales documents from _DATA_START to today.

    On first load (or after cache clear): fetches everything from Holded,
    splits by year, caches historical years to Supabase.

    On subsequent loads: reads historical years from Supabase, re-fetches
    only the current year from Holded.
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
    cached_years = {row["year"]: row for row in db.get_holded_cache_index()}

    # Check if all historical years are cached at the current version
    historical_years  = list(range(_DATA_START, current_year))
    all_cached_valid  = all(
        year in cached_years
        and cached_years[year].get("cache_version", 1) >= _CACHE_VERSION
        for year in historical_years
    )

    all_docs: dict[str, dict] = {}

    if not all_cached_valid:
        # One or more historical years missing or stale — fetch everything
        # from Holded in a single pass, then split and cache by year
        progress = st.progress(0.0, text="Descargando todo el histórico de Holded…")

        for i, doc_type in enumerate(_DOC_TYPES):
            progress.progress(
                i / len(_DOC_TYPES),
                text=f"Descargando {doc_type}s…"
            )
            for doc in _fetch_all_pages(doc_type):
                all_docs[doc["id"]] = doc

        progress.progress(0.9, text="Guardando caché…")

        # Split by year and cache historical years
        by_year = _split_by_year(list(all_docs.values()))
        for year, docs in by_year.items():
            if year < current_year:
                db.save_holded_year_cache(year, docs, _CACHE_VERSION)

        progress.progress(1.0, text=f"✓ {len(all_docs)} documentos cargados")
        time.sleep(0.4)
        progress.empty()

    else:
        # All historical years are cached — load them from Supabase
        # and re-fetch only the current year from Holded
        progress = st.progress(0.0, text="Cargando histórico desde caché…")

        for i, year in enumerate(historical_years):
            progress.progress(
                i / (len(historical_years) + 1),
                text=f"Cargando {year} desde caché…"
            )
            for doc in db.get_holded_year_cache(year):
                all_docs[doc["id"]] = doc

        progress.progress(
            len(historical_years) / (len(historical_years) + 1),
            text=f"Actualizando {current_year} desde Holded…"
        )
        for doc in _fetch_all_pages_current_year():
            all_docs[doc["id"]] = doc

        progress.progress(1.0, text=f"✓ {len(all_docs)} documentos cargados")
        time.sleep(0.4)
        progress.empty()

    docs = sorted(all_docs.values(), key=lambda x: x.get("date", 0))
    st.session_state[session_key] = docs
    st.session_state[ts_key]      = now
    return docs


def _fetch_all_pages_current_year() -> list[dict]:
    """
    Fetch current-year documents only.
    Since Holded returns most-recent first, we fetch pages until we hit
    a document from a previous year, then stop.
    """
    current_year = datetime.now().year
    results: dict[str, dict] = {}

    for doc_type in _DOC_TYPES:
        page = 1
        while True:
            r = requests.get(
                f"{_BASE_URL}/documents/{doc_type}",
                headers=_headers(),
                params={"page": page},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            if data is None:
                break

            batch = (
                data if isinstance(data, list)
                else data.get("documents") or data.get("list") or data.get("data") or []
            )
            if not batch:
                break

            found_older = False
            for doc in batch:
                if doc.get("draft"):
                    continue
                doc_year = datetime.fromtimestamp(
                    doc.get("date", 0), tz=timezone.utc
                ).year
                if doc_year == current_year:
                    doc["_doc_type"] = doc_type
                    results[doc["id"]] = doc
                else:
                    found_older = True

            # If we've hit older docs, all remaining pages will also be older
            if found_older:
                break
            page += 1

    return list(results.values())


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

    doc_type field:
      'invoice'      = Factura (wholesale)
      'salesreceipt' = Ticket de venta (Shopify)
      'creditnote'   = Venta rectificativa (negated)

    line_revenue is negated for credit notes so they correctly
    reduce revenue totals everywhere they are summed.
    """
    rows = []
    for inv in invoices:
        date         = inv.get("date", 0)
        contact_id   = inv.get("contact", "")
        contact_name = (inv.get("contactName") or "").strip()
        doc_number   = inv.get("docNumber") or inv.get("id", "")
        doc_type     = inv.get("_doc_type", "invoice")
        is_credit    = (doc_type == "creditnote")
        corrects     = inv.get("from") or {}
        corrects_id  = corrects.get("id", "") if isinstance(corrects, dict) else ""

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
            sign     = -1 if is_credit else 1
            line_rev = sign * price * units * (1 - discount / 100)

            rows.append({
                "invoice_id":       inv.get("id", ""),
                "doc_number":       doc_number,
                "doc_type":         doc_type,
                "contact_id":       contact_id,
                "contact_name":     contact_name,
                "date":             date,
                "name":             (line.get("name") or "").strip(),
                "sku":              sku,
                "price":            price,
                "units":            units,
                "discount":         discount,
                "tax":              tax,
                "line_revenue":     line_rev,
                "is_product":       bool(sku),
                "corrects_invoice": corrects_id,
            })

    return rows


# =============================================================================
# Revenue helpers
# =============================================================================

def signed_subtotal(doc: dict) -> float:
    """
    Return the ex-VAT revenue contribution of a document.
    Holded stores credit note fields as positive — we negate them.
    """
    subtotal = float(doc.get("subtotal") or 0)
    if doc.get("_doc_type") == "creditnote":
        return -subtotal
    return subtotal
