# core/settings.py
# =============================================================================
# AppSettings dataclass — single source of truth for all business settings.
#
# Usage in any screen:
#
#   from core.settings import load_settings
#   s = load_settings()
#   margin = s.ws_margin
#
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass
import millington_db as db


@dataclass(frozen=True)
class AppSettings:
    """Typed, immutable snapshot of the settings table."""

    # Labour & oven
    default_labour_rate:  float   # €/hr
    default_oven_rate:    float   # €/hr
    labour_power:         float   # power-law batch scaling exponent

    # Margins (multipliers applied to cost)
    ws_margin:            float
    rt_margin_large:      float
    rt_margin_individual: float
    rt_margin_bocado:     float

    # Wholesale batch sizes
    ws_batch_large:       int
    ws_batch_individual:  int
    ws_batch_bocado:      int

    # Retail batch sizes
    rt_batch_large:       int
    rt_batch_individual:  int
    rt_batch_bocado:      int

    # Default unit weights for Individual / Bocado formats
    individual_weight_g:  float
    bocado_weight_g:      float

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def ws_batch(self, fmt: str) -> int:
        """Return wholesale batch size for a format key ('standard' / 'individual' / 'bocado')."""
        return {
            "standard":   self.ws_batch_large,
            "individual": self.ws_batch_individual,
            "bocado":     self.ws_batch_bocado,
        }.get(fmt.lower(), self.ws_batch_large)

    def rt_batch(self, fmt: str) -> int:
        """Return retail batch size for a format key."""
        return {
            "standard":   self.rt_batch_large,
            "individual": self.rt_batch_individual,
            "bocado":     self.rt_batch_bocado,
        }.get(fmt.lower(), self.rt_batch_large)

    def rt_margin(self, fmt: str) -> float:
        """Return retail margin multiplier for a format key."""
        return {
            "standard":   self.rt_margin_large,
            "individual": self.rt_margin_individual,
            "bocado":     self.rt_margin_bocado,
        }.get(fmt.lower(), self.rt_margin_large)


def load_settings() -> AppSettings:
    """
    Fetch settings from the database and return a typed AppSettings instance.
    Defaults mirror those previously hardcoded in each screen.
    """
    s = db.get_settings()
    return AppSettings(
        default_labour_rate  = float(s.get("default_labour_rate")   or 30.0),
        default_oven_rate    = float(s.get("default_oven_rate")      or 2.0),
        labour_power         = float(s.get("labour_power")           or 0.7),
        ws_margin            = float(s.get("ws_margin")              or 2.0),
        rt_margin_large      = float(s.get("rt_margin_large")        or 3.0),
        rt_margin_individual = float(s.get("rt_margin_individual")   or 3.0),
        rt_margin_bocado     = float(s.get("rt_margin_bocado")       or 3.0),
        ws_batch_large       = int(s.get("ws_batch_large")           or 20),
        ws_batch_individual  = int(s.get("ws_batch_individual")      or 100),
        ws_batch_bocado      = int(s.get("ws_batch_bocado")          or 250),
        rt_batch_large       = int(s.get("rt_batch_large")           or 1),
        rt_batch_individual  = int(s.get("rt_batch_individual")      or 4),
        rt_batch_bocado      = int(s.get("rt_batch_bocado")          or 25),
        individual_weight_g  = float(s.get("individual_weight_g")    or 100),
        bocado_weight_g      = float(s.get("bocado_weight_g")        or 30),
    )
