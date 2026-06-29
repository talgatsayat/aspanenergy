"""Extract structured data from utility bills (PDFs / images / screenshots).

Strategy:
  1. Render every input file to one or more PNG page images.
  2. Send the images to a vision LLM (Anthropic Claude or OpenAI GPT) with a
     strict JSON schema describing the fields we need.
  3. Validate the response into MonthlyBill objects with a confidence score, and
     infer the client identity (name / address / connection power) so the app
     can pre-fill the client form for review.

The CIE bills in this project are scanned images in French (no text layer),
so a vision model is the robust choice over classic OCR.

The engine never fabricates figures: without an API key it runs in ``manual``
mode (empty rows for hand entry), and a bill that cannot be read is left blank.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import config as _config  # triggers .env loading on import
from .geo import parse_dms, parse_latlng
from .schema import BuildingRoof, MonthlyBill, SiteReport

# Fields we ask the model to return for each bill.
EXTRACTION_PROMPT = """You are a precise data-extraction engine for electricity bills.
These are CIE (Cote d'Ivoire) medium-voltage utility bills, in French.

The bill has a billing detail table ("DETAILS DE LA FACTURATION" / "TRANCHES")
with one row per time band:
  - "Jour"   = day energy
  - "Pointe" = peak energy (counts as daytime)
  - "Nuit"   = night energy
Each row shows: CONSOMMATION kWh | PRIX Unit. (FCFA/kWh) | MONTANT HT.
"TOTAL ACTIF" is the total active energy in kWh (= Jour + Pointe + Nuit).

Extract, for THIS bill:
  - total_kwh : the "TOTAL ACTIF" kWh value (industrial site: ~150,000-350,000)
  - day_kwh   : Jour kWh + Pointe kWh
  - night_kwh : Nuit kWh
  - day_tariff: the "Jour" unit price (PRIX Unit., FCFA/kWh, ~80-90)
  - total_cost: the final total invoice amount (MONTANT TOTAL FACTURE / TTC), FCFA
  - period    : billing month as "YYYY.MM"

Also read the customer identity printed on the bill (usually top section):
  - client_name : the subscriber / company name on the bill
  - client_address : the supply address (street / zone) if shown
  - client_city : the CIE "EXPLOITATION" agency town / commune (e.g.
    "PORT-BOUET", "TREICHVILLE"), or the city in the address. This is the most
    reliable location on the bill.
  - client_country : the country. For a CIE (Compagnie Ivoirienne d'Electricite)
    bill this is "Cote d'Ivoire".
  - subscribed_power_kva : subscribed/connection power in kVA if shown
    ("PUISSANCE SOUSCRITE" / "PUISSANCE TRANSFORMATEUR", e.g. 630)

Read the numbers carefully; French bills use spaces as thousands separators
(e.g. "256 816" = 256816) and may use a comma decimal ("83,87" = 83.87).

Return STRICT JSON only, no prose, exactly:
{"period": "YYYY.MM", "total_kwh": number, "day_kwh": number|null,
 "night_kwh": number|null, "total_cost": number|null, "day_tariff": number|null,
 "client_name": string|null, "client_address": string|null,
 "client_city": string|null, "client_country": string|null,
 "subscribed_power_kva": number|null, "confidence": number}
Use null for any value you cannot read, and lower the confidence accordingly.
Numbers must be plain (no spaces, commas-as-thousands, or currency symbols)."""


@dataclass
class ExtractionResult:
    bills: List[MonthlyBill]
    mode: str          # "anthropic" | "openai" | "manual"
    warnings: List[str]
    # Client identity inferred from the bills (name/address/kVA), all optional.
    client_info: Dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Rendering inputs to images
# --------------------------------------------------------------------------
def _render_to_pngs(path: str, dpi: int = 150) -> List[str]:
    """Return a list of PNG paths for a PDF (per page) or image file.

    PDF rasterization tries, in order:
      1. PyMuPDF (pip-only, no system dependency)  <- preferred
      2. pdftoppm (Poppler) if installed on the system
    """
    p = Path(path)
    suffix = p.suffix.lower()
    out_dir = Path(tempfile.mkdtemp(prefix="aspan_bill_"))

    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        return [str(p)]

    if suffix != ".pdf":
        raise ValueError(f"Unsupported file type: {suffix}")

    # 1) PyMuPDF — self-contained, recommended
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(p))
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        out = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat)
            fp = out_dir / f"page-{i + 1:02d}.png"
            pix.save(str(fp))
            out.append(str(fp))
        doc.close()
        if out:
            return out
    except ImportError:
        pass

    # 2) pdftoppm (Poppler), if available
    try:
        prefix = out_dir / "page"
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), str(p), str(prefix)],
            check=True, capture_output=True,
        )
        pages = sorted(str(f) for f in out_dir.glob("page*.png"))
        if pages:
            return pages
    except FileNotFoundError:
        pass

    raise RuntimeError(
        "Cannot rasterize PDF: install PyMuPDF ('pip install pymupdf') "
        "or Poppler ('brew install poppler' / 'apt-get install poppler-utils').")


def _b64(path: str) -> str:
    return base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")


def _period_from_name(path: str) -> str:
    """Guess a period like '2025.06' from a filename such as '2025.06.pdf'."""
    m = re.search(r"(20\d{2})[.\-_](\d{2})", Path(path).stem)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return Path(path).stem


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------
def _provider() -> str:
    forced = os.getenv("ASPAN_EXTRACTOR", "").lower()
    if forced in ("anthropic", "openai", "manual"):
        return forced
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "manual"


def _call_anthropic(png_paths: List[str], prompt: str,
                    max_pages: int = 2, max_tokens: int = 1024) -> str:
    content = [{"type": "text", "text": prompt}]
    for png in png_paths[:max_pages]:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": _b64(png)},
        })
    messages = [{"role": "user", "content": content}]
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    try:  # preferred: official SDK
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model, max_tokens=max_tokens, messages=messages)
        return msg.content[0].text
    except ImportError:  # fallback: raw REST via requests (no SDK needed)
        import requests

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "messages": messages},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]


def _call_openai(png_paths: List[str], prompt: str,
                 max_pages: int = 2, max_tokens: int = 1024) -> str:
    content = [{"type": "text", "text": prompt}]
    for png in png_paths[:max_pages]:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_b64(png)}", "detail": "high"},
        })
    messages = [{"role": "user", "content": content}]
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    try:  # preferred: official SDK
        from openai import OpenAI

        client = OpenAI()
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=messages)
        return resp.choices[0].message.content
    except ImportError:  # fallback: raw REST via requests (no SDK needed)
        import requests

        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                     "Content-Type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "messages": messages},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _call_vision(mode: str, png_paths: List[str], prompt: str,
                 max_pages: int = 2, max_tokens: int = 1024) -> str:
    fn = _call_anthropic if mode == "anthropic" else _call_openai
    return fn(png_paths, prompt, max_pages=max_pages, max_tokens=max_tokens)


def _parse_json_obj(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


# --------------------------------------------------------------------------
# Empty placeholder for manual entry (NO fabricated numbers)
# --------------------------------------------------------------------------
def _empty_bill(path: str) -> MonthlyBill:
    """A blank row for the operator to fill in by hand (confidence 0)."""
    return MonthlyBill(
        period=_period_from_name(path),
        total_kwh=0.0,
        day_kwh=None,
        night_kwh=None,
        day_tariff=None,
        total_cost=None,
        confidence=0.0,   # 0 = no data yet; never a guessed value
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def extract_bills(file_paths: List[str], mode: Optional[str] = None) -> ExtractionResult:
    """Extract a MonthlyBill from each input file.

    The engine never fabricates figures: if no vision provider is configured,
    or extraction fails for a bill, the row is returned EMPTY (total 0,
    confidence 0) for the operator to fill in by hand. This keeps the financial
    proposal honest — only real, reviewed numbers reach a client.
    """
    mode = (mode or _provider()).lower()
    bills: List[MonthlyBill] = []
    warnings: List[str] = []

    if mode == "manual":
        for path in file_paths:
            bills.append(_empty_bill(path))
        warnings.append(
            "No vision provider configured (no API key). Rows are empty - enter "
            "the consumption manually, or set ANTHROPIC_API_KEY / OPENAI_API_KEY "
            "for automatic extraction.")
        return ExtractionResult(bills=bills, mode=mode, warnings=warnings)

    raws: List[dict] = []
    for path in file_paths:
        fallback_period = _period_from_name(path)
        try:
            pngs = _render_to_pngs(path, dpi=200)
            text = _call_vision(mode, pngs, EXTRACTION_PROMPT, max_pages=2)
            raw = _parse_json_obj(text)
            if not raw.get("period"):
                raw["period"] = fallback_period
            bills.append(_to_bill(raw, fallback_period))
            raws.append(raw)
        except Exception as exc:  # never crash; leave the row blank for review
            warnings.append(
                f"{Path(path).name}: extraction failed ({exc}). Row left blank - "
                f"please enter the values manually.")
            bills.append(_empty_bill(path))

    return ExtractionResult(bills=bills, mode=mode, warnings=warnings,
                            client_info=_aggregate_client_info(raws))


def _aggregate_client_info(raws: List[dict]) -> Dict[str, object]:
    """Infer a single client identity from per-bill extractions.

    Uses the most frequent non-empty name/address (bills repeat the same
    subscriber) and the most common subscribed power.
    """
    def most_common_str(key: str) -> Optional[str]:
        vals = [str(r.get(key)).strip() for r in raws
                if r.get(key) and str(r.get(key)).strip().lower() != "null"]
        return Counter(vals).most_common(1)[0][0] if vals else None

    def most_common_num(key: str) -> Optional[float]:
        vals = []
        for r in raws:
            v = r.get(key)
            try:
                if v is not None:
                    vals.append(float(v))
            except (TypeError, ValueError):
                continue
        return Counter(vals).most_common(1)[0][0] if vals else None

    info: Dict[str, object] = {}
    name = most_common_str("client_name")
    address = most_common_str("client_address")
    city = most_common_str("client_city")
    country = most_common_str("client_country")
    kva = most_common_num("subscribed_power_kva")
    if name:
        info["name"] = name
    if address:
        info["address"] = address
    if city:
        info["city"] = city
    if country:
        info["country"] = country
    if kva:
        info["grid_capacity_kva"] = kva
    return info


def _to_bill(raw: dict, fallback_period: str) -> MonthlyBill:
    def num(key):
        v = raw.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return MonthlyBill(
        period=str(raw.get("period") or fallback_period),
        total_kwh=num("total_kwh") or 0.0,
        day_kwh=num("day_kwh"),
        night_kwh=num("night_kwh"),
        total_cost=num("total_cost"),
        day_tariff=num("day_tariff"),
        confidence=float(raw.get("confidence", 0.8)),
    )


# --------------------------------------------------------------------------
# Technical site / feasibility report extraction
# --------------------------------------------------------------------------
REPORT_PROMPT = """You are extracting data from a SOLAR TECHNICAL / FEASIBILITY
SITE-VISIT REPORT (often in French, e.g. "RAPPORT TECHNIQUE D'EXPERTISE ET DE
FAISABILITE PHOTOVOLTAIQUE"). Read all pages.

Extract:
  - latitude, longitude : site GPS in DECIMAL degrees. The report may give DMS
    like 5°15'53.8"N 4°00'12.4"W -> convert (W and S are negative).
  - gps_raw : the GPS string exactly as printed (fallback if you can't convert).
  - transformer_kva : the CIE transformer / connection rating in kVA (e.g. 1250).
  - diesel_kw : backup diesel generator power in kW (e.g. 1100), or null.
  - recommended_kwc : the total recommended PV capacity in kWc (e.g. 600).
  - n_modules : number of PV modules/panels (e.g. 1000), or null.
  - buildings : array of the per-building roof allocation table, each
    {"name": str, "area_m2": number|null, "capacity_kwc": number|null}.
  - feasibility_verdict : one concise sentence stating whether the project is
    technically feasible (quote/paraphrase the report's conclusion), or null.
  - engineer_name : the signing engineer's name if present, else null.
  - client_name : the client / company the report is about (e.g. "NESKAO"), or null.
  - client_city : the site city / commune (e.g. "Abidjan"), or null.
  - client_country : the country (e.g. "Cote d'Ivoire"), or null.
  - confidence : 0..1.

Numbers must be plain (no spaces/thousands separators). Return STRICT JSON only:
{"latitude": number|null, "longitude": number|null, "gps_raw": string|null,
 "transformer_kva": number|null, "diesel_kw": number|null,
 "recommended_kwc": number|null, "n_modules": number|null,
 "buildings": [{"name": string, "area_m2": number|null, "capacity_kwc": number|null}],
 "feasibility_verdict": string|null, "engineer_name": string|null,
 "client_name": string|null, "client_city": string|null,
 "client_country": string|null, "confidence": number}"""


def extract_report(file_path: str, mode: Optional[str] = None):
    """Extract a SiteReport from a technical/feasibility report.

    Returns (SiteReport | None, warnings). None when no provider is configured
    or extraction fails — the proposal then simply omits the feasibility slide.
    """
    mode = (mode or _provider()).lower()
    warnings: List[str] = []
    if mode == "manual":
        return None, ["No vision provider configured - the site report cannot be "
                      "read automatically. Set ANTHROPIC_API_KEY / OPENAI_API_KEY."]
    try:
        pngs = _render_to_pngs(file_path, dpi=170)
        text = _call_vision(mode, pngs, REPORT_PROMPT, max_pages=6, max_tokens=2048)
        raw = _parse_json_obj(text)
        return _to_report(raw), warnings
    except Exception as exc:
        return None, [f"{Path(file_path).name}: report extraction failed ({exc})."]


def _to_report(raw: dict) -> SiteReport:
    def num(key):
        v = raw.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    lat, lng = num("latitude"), num("longitude")
    if (lat is None or lng is None) and raw.get("gps_raw"):
        parsed = parse_latlng(str(raw["gps_raw"]))
        if parsed:
            lat, lng = parsed

    buildings = []
    for b in raw.get("buildings") or []:
        if not isinstance(b, dict) or not b.get("name"):
            continue
        try:
            area = float(b["area_m2"]) if b.get("area_m2") is not None else None
        except (TypeError, ValueError):
            area = None
        try:
            kwc = float(b["capacity_kwc"]) if b.get("capacity_kwc") is not None else None
        except (TypeError, ValueError):
            kwc = None
        buildings.append(BuildingRoof(name=str(b["name"]), area_m2=area, capacity_kwc=kwc))

    n_mod = num("n_modules")
    return SiteReport(
        latitude=lat,
        longitude=lng,
        transformer_kva=num("transformer_kva"),
        diesel_kw=num("diesel_kw"),
        recommended_kwc=num("recommended_kwc"),
        n_modules=int(n_mod) if n_mod else None,
        buildings=buildings,
        feasibility_verdict=(str(raw["feasibility_verdict"])
                             if raw.get("feasibility_verdict") else None),
        engineer_name=(str(raw["engineer_name"]) if raw.get("engineer_name") else None),
        client_name=(str(raw["client_name"]) if raw.get("client_name") else None),
        client_city=(str(raw["client_city"]) if raw.get("client_city") else None),
        client_country=(str(raw["client_country"]) if raw.get("client_country") else None),
        confidence=float(raw.get("confidence", 0.8)),
    )
