"""Small formatting helpers (numbers, money) — bilingual FR/EN."""
from __future__ import annotations


def fmt_int(value: float) -> str:
    """Thousands separated by a thin space: 870000 -> '870 000'."""
    return f"{int(round(value)):,}".replace(",", " ")


def fmt_money(value: float, lang: str = "fr", currency: str = "FCFA") -> str:
    """Compact money: 14_589_900 -> '14,6 M FCFA' (fr) / '14.6 M FCFA' (en)."""
    av = abs(value)
    if av >= 1_000_000_000:
        unit = "Md" if lang == "fr" else "B"
        num = value / 1_000_000_000
        s = f"{num:.2f}".rstrip("0").rstrip(".")
    elif av >= 1_000_000:
        unit = "M"
        num = value / 1_000_000
        s = f"{num:.1f}"
    elif av >= 1_000:
        unit = "k"
        num = value / 1_000
        s = f"{num:.0f}"
    else:
        unit = ""
        s = f"{value:.0f}"
    if lang == "fr":
        s = s.replace(".", ",")
    return f"{s} {unit} {currency}".replace("  ", " ").strip()


def fmt_kwh(value: float) -> str:
    return f"{fmt_int(value)} kWh"


def t(lang: str, fr: str, en: str) -> str:
    """Pick the right language string."""
    return fr if lang == "fr" else en
