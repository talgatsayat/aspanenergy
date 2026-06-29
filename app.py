"""Aspan Proposal Engine — Streamlit web app.

Workflow (target: < 5 minutes):
  1. Upload utility bills  ->  auto-extract & review the numbers
  2. Fill client info (smart defaults) + roof area / geolocation
  3. Preview the recommended system & savings potential
  4. Generate the branded .pptx proposal and download it

Run:  streamlit run app.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from aspan import config
from aspan.email_gen import build_cover_email
from aspan.engine import build_proposal_data
from aspan.extraction import extract_bills, extract_report
from aspan.template_builder import build_from_template
from aspan.format_utils import fmt_int, fmt_money
from aspan.geo import (
    geocode,
    google_maps_embed_url,
    google_solar_insights,
    maps_link,
    panels_from_area,
    parse_latlng,
    pvgis_specific_yield,
    static_map_url,
)
from aspan.pptx_builder import build_pptx
from aspan.schema import ClientInfo, MonthlyBill

st.set_page_config(page_title="Aspan Proposal Engine", page_icon="*", layout="wide")


# Cached geo lookups — avoid re-hitting the network on every widget interaction.
@st.cache_data(show_spinner=False)
def _cached_geocode(address: str):
    return geocode(address)


@st.cache_data(show_spinner=False)
def _cached_pvgis(lat: float, lng: float):
    return pvgis_specific_yield(lat, lng)


@st.cache_data(show_spinner=False)
def _cached_solar(lat: float, lng: float):
    return google_solar_insights(lat, lng)

PRIMARY = "#F2A900"
st.markdown(
    f"""<style>
    .stApp h1, .stApp h2, .stApp h3 {{ color: #0B1F2A; }}
    div[data-testid="stMetricValue"] {{ color: {PRIMARY}; }}
    </style>""",
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------
st.title("ASPAN ENERGY - Proposal Engine")
st.caption("Generate a professional solar PPA proposal in minutes.")

if "bills" not in st.session_state:
    st.session_state.bills = None

with st.sidebar:
    st.header("Settings")
    lang = st.radio("Proposal language", ["fr", "en"],
                    format_func=lambda x: "Francais" if x == "fr" else "English")
    deck_style = st.radio(
        "Deck", ["template", "extended"],
        format_func=lambda x: ("Aspan template (their exact deck)" if x == "template"
                               else "Extended (+ summary, feasibility, financials)"))
    st.divider()
    st.subheader("Extraction engine")
    from aspan.extraction import _provider
    mode = _provider()
    if mode == "manual":
        st.warning("No API key found - manual entry mode. Uploaded bills give "
                   "empty rows to fill in by hand (no fabricated numbers). Set "
                   "ANTHROPIC_API_KEY or OPENAI_API_KEY for automatic extraction.")
    else:
        st.success(f"Vision extraction: **{mode}**")
    st.caption("Assumptions are editable in config/assumptions.yaml")

# --------------------------------------------------------------------------
# Step 1 — Bills
# --------------------------------------------------------------------------
st.header("1. Utility bills")
uploads = st.file_uploader(
    "Upload electricity bills (PDF / image). You can select several months.",
    type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True,
)
report_upload = st.file_uploader(
    "Optional: technical / feasibility site report (PDF). Adds exact GPS, the "
    "per-building roof breakdown and a feasibility slide to the proposal.",
    type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=False,
)

col_a, col_b = st.columns([1, 3])
with col_a:
    if st.button("Extract", type="primary", disabled=not uploads):
        tmpdir = Path(tempfile.mkdtemp(prefix="aspan_up_"))
        paths = []
        for up in uploads:
            fp = tmpdir / up.name
            fp.write_bytes(up.getbuffer())
            paths.append(str(fp))
        with st.spinner("Reading bills..."):
            result = extract_bills(paths)
        st.session_state.bills = result
        # Optional site report
        st.session_state["site_report"] = None
        st.session_state["report_warnings"] = []
        if report_upload is not None:
            rp = tmpdir / report_upload.name
            rp.write_bytes(report_upload.getbuffer())
            with st.spinner("Reading site report..."):
                report, rwarn = extract_report(str(rp))
            st.session_state["site_report"] = report
            st.session_state["report_warnings"] = rwarn
            if report and report.latitude is not None and report.longitude is not None:
                st.session_state["f_coords"] = f"{report.latitude:.5f}, {report.longitude:.5f}"
            if report and report.transformer_kva:
                st.session_state["f_grid"] = float(report.transformer_kva)
            # A backup diesel generator in the report -> tick the diesel box
            if report and report.diesel_kw:
                st.session_state["f_diesel"] = True
            # Text client fields from the report fill in ONLY if the bills didn't
            def _fill_if_empty(key, val):
                if val and not str(st.session_state.get(key, "")).strip():
                    st.session_state[key] = str(val)
            if report:
                _fill_if_empty("f_name", report.client_name)
                _fill_if_empty("f_country", report.client_country)
                if report.client_city and not st.session_state.get("client_city"):
                    st.session_state["client_city"] = str(report.client_city)
            # Available roof area = sum of the per-building roof areas in the report
            if report and report.buildings:
                total_roof = sum(b.area_m2 or 0 for b in report.buildings)
                if total_roof > 0:
                    st.session_state["f_roof"] = float(total_roof)
        # Auto-fill the client form from what the bills reveal (editable later).
        ci = result.client_info or {}
        if ci.get("name"):
            st.session_state["f_name"] = str(ci["name"])
        if ci.get("address"):
            st.session_state["f_address"] = str(ci["address"])
        if ci.get("country"):
            st.session_state["f_country"] = str(ci["country"])
        if ci.get("grid_capacity_kva"):
            try:
                st.session_state["f_grid"] = float(ci["grid_capacity_kva"])
            except (TypeError, ValueError):
                pass
        # City/commune isn't a form field but drives approximate geolocation.
        st.session_state["client_city"] = str(ci.get("city") or "")
        st.session_state["client_autofilled"] = bool(ci)
with col_b:
    if uploads:
        st.caption(f"{len(uploads)} file(s) ready.")

bill_rows = []
if st.session_state.bills:
    result = st.session_state.bills
    for w in result.warnings:
        st.info(w)
    for w in st.session_state.get("report_warnings", []):
        st.info(w)
    sr = st.session_state.get("site_report")
    if sr:
        bits = []
        if sr.latitude is not None:
            bits.append(f"GPS {sr.latitude:.4f}, {sr.longitude:.4f}")
        if sr.transformer_kva:
            bits.append(f"transformer {sr.transformer_kva:.0f} kVA")
        if sr.buildings:
            total_roof = sum(b.area_m2 or 0 for b in sr.buildings)
            bits.append(f"{len(sr.buildings)} buildings"
                        + (f", roof {total_roof:.0f} m2" if total_roof else ""))
        st.success("Site report read: " + ", ".join(bits) +
                   ". A feasibility slide will be added; GPS, grid and roof area "
                   "pre-filled.")
    st.subheader("Review extracted data")
    st.caption("Edit any value before generating. Low confidence rows are flagged.")
    import pandas as pd

    df = pd.DataFrame([{
        "period": b.period,
        "total_kwh": b.total_kwh,
        "day_kwh": round(b.derived_day_kwh),
        "day_tariff": b.day_tariff,
        "confidence": b.confidence,
    } for b in result.bills])
    edited = st.data_editor(df, use_container_width=True, hide_index=True,
                            key="bill_editor")
    for _, r in edited.iterrows():
        bill_rows.append(MonthlyBill(
            period=str(r["period"]),
            total_kwh=float(r["total_kwh"]),
            day_kwh=float(r["day_kwh"]) if r["day_kwh"] else None,
            day_tariff=float(r["day_tariff"]) if r["day_tariff"] else None,
            confidence=float(r["confidence"]),
        ))

# --------------------------------------------------------------------------
# Step 2 — Client info
# --------------------------------------------------------------------------
st.header("2. Client information")
if st.session_state.get("client_autofilled"):
    st.success("Client details pre-filled from the uploaded bills - please review "
               "and correct anything that looks wrong.")
else:
    st.caption("Upload and extract bills to auto-fill these, or type them in.")
c1, c2, c3 = st.columns(3)
with c1:
    name = st.text_input("Client name", key="f_name",
                         placeholder="e.g. Acme Industries")
    industry = st.text_input("Industry", key="f_industry",
                             placeholder="e.g. Manufacturing")
    country = st.text_input("Country", key="f_country",
                            placeholder="e.g. Cote d'Ivoire")
with c2:
    grid_kva = st.number_input("Grid connection (kVA)", min_value=0.0,
                               step=50.0, key="f_grid",
                               help="0 = unknown (no grid-capacity limit applied).")
    has_diesel = st.checkbox("Diesel generator on site", key="f_diesel")
    reference = st.text_input("Proposal reference", key="f_ref",
                              placeholder="e.g. OF-2026-001")
with c3:
    address = st.text_input("Site address", key="f_address",
                            placeholder="e.g. Industrial Zone, City")
    description = st.text_area("Short business description", height=70,
                              key="f_desc", placeholder="What the company does")

st.subheader("Roof & geolocation")
st.caption("Coordinates drive the site map, the PVGIS production yield and the "
           "Google Solar roof analysis. Type GPS directly, or enter the address "
           "above to resolve it automatically (no API key needed).")

gcol1, gcol2 = st.columns([1, 1])
with gcol1:
    coords = st.text_input("GPS coordinates (lat,lng or DMS)", key="f_coords",
                           placeholder="e.g. 5.2649, -4.0034")
    st.caption("Leave blank to auto-locate from the address above.")

# Resolve coordinates. Priority:
#   1) GPS the operator typed
#   2) city/commune + country from the bill (reliable, city-level is enough for PVGIS)
#   3) the raw supply address (often redacted on CIE bills)
latlng = parse_latlng(coords) if coords else None
geo_source = "typed" if latlng else None
if not latlng:
    city = st.session_state.get("client_city", "").strip()
    locality = ", ".join(p for p in [city, country.strip()] if p)
    for query, src in [(locality, "city"), (address.strip(), "address")]:
        if not query:
            continue
        resolved = _cached_geocode(query)
        if resolved:
            latlng = tuple(resolved)
            geo_source = src
            break

# Geo-derived services (cached so widget interactions don't re-hit the network)
solar = _cached_solar(*latlng) if latlng else None
pvgis = _cached_pvgis(*latlng) if latlng else None

with gcol2:
    if latlng:
        lat, lng = latlng
        msg = {
            "typed": "Using the GPS you entered.",
            "city": "Approx. location from the bill's city/country.",
            "address": "Located from the supply address.",
        }.get(geo_source, "Location resolved.")
        st.success(f"{msg}  ({lat:.4f}, {lng:.4f})")
        if geo_source == "city":
            st.caption("City-level point - fine for PVGIS yield. For exact roof "
                       "(Google Solar) paste the precise GPS from Google Maps.")
        st.markdown(f"[Open site on Google Maps]({maps_link(lat, lng)})")
    elif address.strip() or st.session_state.get("client_city"):
        st.warning("Could not resolve a location automatically - type the GPS "
                   "directly (lat,lng).")
    else:
        st.caption("No location yet - add an address/GPS (or extract bills with a "
                   "city) to enable the map, PVGIS yield and Solar roof analysis.")

g1, g2 = st.columns([1, 1])
with g1:
    manual_roof = st.number_input("Available roof area (m2)", min_value=0.0,
                                  step=50.0, key="f_roof",
                                  help="Auto-filled from the site report when provided; "
                                       "otherwise from the survey or Google Maps. "
                                       "0 = unknown (no roof limit applied).")
    use_google = False
    if solar:
        st.success(f"Google Solar detected this building: "
                   f"~{fmt_int(solar.roof_area_m2)} m2 roof, up to {solar.max_panels} panels "
                   f"(~{fmt_int(solar.google_capacity_kwc)} kWc).")
        use_google = st.checkbox("Use Google-detected roof area for sizing", value=True)
    roof_area = solar.roof_area_m2 if (solar and use_google) else manual_roof
    if roof_area > 0:
        est = panels_from_area(roof_area)
        st.metric("Roof-based capacity", f"{fmt_int(est.capacity_kwc)} kWc",
                  help=f"~{est.n_panels} modules of {est.module_power_wp} Wp")
        st.caption(f"{fmt_int(roof_area)} m2 -> ~{est.n_panels} panels "
                   f"-> {fmt_int(est.capacity_kwc)} kWc installable.")
with g2:
    if latlng:
        # Interactive Google map, inline, no API key required.
        components.html(
            f'<iframe width="100%" height="280" style="border:0;border-radius:10px" '
            f'loading="lazy" referrerpolicy="no-referrer-when-downgrade" '
            f'src="{google_maps_embed_url(*latlng)}"></iframe>',
            height=295,
        )
        # If a Google Maps key is set, also offer a crisp satellite still.
        sat = static_map_url(*latlng)
        if sat:
            st.image(sat, caption="Satellite view")
        default_y = config.assumptions()["solar"]["yield_kwh_per_kwc"]
        if pvgis:
            st.metric("PVGIS specific yield",
                      f"{fmt_int(pvgis.specific_yield_kwh_per_kwc)} kWh/kWc",
                      delta=f"{pvgis.specific_yield_kwh_per_kwc - default_y:+.0f} vs default",
                      help="Real site irradiance from EU PVGIS (free, key-less). "
                           "Production is computed from this value.")
        else:
            st.caption(f"PVGIS yield unavailable here - using the default "
                       f"{fmt_int(default_y)} kWh/kWc.")
        if not solar:
            st.caption("Google Solar API: no coverage here (or Solar API not "
                       "enabled) - using the area-based roof estimate.")

# --------------------------------------------------------------------------
# Step 3 — Preview & generate
# --------------------------------------------------------------------------
st.header("3. Recommendation & proposal")

has_real_data = bool(bill_rows) and sum(b.total_kwh for b in bill_rows) > 0

if bill_rows and not has_real_data:
    st.warning("No consumption values yet. Fill in the monthly kWh in the table "
               "above (or add an API key for automatic extraction) before "
               "generating the proposal.")

if has_real_data:
    client = ClientInfo(
        name=name.strip() or "Client", industry=industry.strip(),
        country=country.strip(), description=description.strip(),
        has_diesel=has_diesel, grid_capacity_kva=grid_kva,
        roof_area_m2=roof_area or None, language=lang,
        latitude=latlng[0] if latlng else None,
        longitude=latlng[1] if latlng else None, address=address.strip() or None,
    )
    sy = pvgis.specific_yield_kwh_per_kwc if pvgis else None
    ysrc = f"PVGIS @ {latlng[0]:.3f},{latlng[1]:.3f}" if pvgis else "default"
    ref = reference.strip() or "OF-PROPOSAL-001"
    data = build_proposal_data(client, bill_rows, reference=ref,
                               specific_yield=sy, yield_source=ysrc,
                               site_report=st.session_state.get("site_report"))
    sz = data.sizing
    s1, s2 = data.scenarios

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Recommended system", f"{fmt_int(sz.recommended_kwc)} kWc")
    m2.metric("Annual production", f"{fmt_int(sz.annual_production_kwh)} kWh")
    m3.metric("Annual saving (Sc.1)", fmt_money(s1.annual_saving, lang))
    m4.metric("15-yr saving (Sc.1)", fmt_money(s1.cumulative_15y_saving, lang))
    fn = data.financial
    n1, n2, n3, n4 = st.columns(4)
    n1.metric("NPV of savings", fmt_money(fn.npv_savings_escalated, lang))
    n2.metric("Consumption covered", f"{fn.solar_coverage_pct:.0f}%")
    n3.metric("CO2 avoided / yr", f"{fmt_int(fn.co2_tonnes_per_year)} t")
    n4.metric("Scenario 2 saving/yr", fmt_money(s2.annual_saving, lang))
    st.caption(f"Binding constraint: **{sz.binding_constraint}**  -  "
               f"Solar tariff {data.solar_tariff:.2f} vs CIE {data.cie_day_tariff:.2f} FCFA/kWh  -  "
               f"discount rate {fn.discount_rate*100:.0f}%, CIE escalation {fn.tariff_escalation*100:.0f}%/yr.")

    if data.bill_summary.lowest_confidence < 0.7:
        st.warning("Some bill values have low extraction confidence. Please review "
                   "Step 1 before sending this proposal to a client.")

    if st.button("Generate proposal (.pptx)", type="primary"):
        config.reload()
        out = Path(tempfile.mkdtemp(prefix="aspan_out_")) / \
            f"Aspan_Proposal_{name.replace(' ', '_')}_{lang}.pptx"
        with st.spinner("Building deck..."):
            if deck_style == "template":
                site_city = st.session_state.get("client_city") or "Abidjan"
                build_from_template(data, str(out), city=site_city)
            else:
                build_pptx(data, str(out))
        st.success("Proposal ready.")
        st.download_button("Download proposal", data=out.read_bytes(),
                           file_name=out.name,
                           mime="application/vnd.openxmlformats-officedocument."
                                "presentationml.presentation")

    # Cover email (AI or template)
    st.subheader("Cover email")
    st.caption("A ready-to-send note to attach the proposal to.")
    if st.button("Generate cover email"):
        with st.spinner("Writing email..."):
            email = build_cover_email(data, lang)
        st.caption(f"Mode: {email['mode']}")
        st.text_input("Subject", email["subject"])
        st.text_area("Body", email["body"], height=320)
        st.download_button(
            "Download email (.txt)",
            data=f"Subject: {email['subject']}\n\n{email['body']}",
            file_name=f"Cover_email_{name.replace(' ', '_')}_{lang}.txt")
else:
    st.info("Upload and extract at least one bill to see the recommendation.")
