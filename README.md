# Aspan Proposal Engine

Generate a professional, client-ready solar **PPA proposal** (`.pptx`) from a
client's electricity bills in **under 5 minutes**, with minimal manual work.

Built as the first version of an internal tool for **Aspan Energy** — upload
utility bills, confirm a few facts about the site, and download a branded,
editable PowerPoint that a CFO or business owner can act on.

---

## What it does

```
Utility bills (scanned PDF/JPG, French)        Client info (form / JSON)
                 |                                         |
        Vision extraction (Claude/GPT)            Roof area / geolocation
         -> kWh, tariffs, costs  ──────────┐      -> panels -> capacity
                                            v
                                   Calculation engine
                       PV sizing  +  production  +  2 savings scenarios
                                            |
                                            v
                          Branded, editable .pptx proposal (FR / EN)
```

Key capabilities:

- **Reads scanned bills automatically.** The CIE bills are image-only PDFs in
  French; a vision LLM extracts monthly kWh, tariffs and costs into a structured,
  reviewable table (with per-bill confidence flags). It never invents figures —
  if a bill can't be read, the row stays blank for manual entry.
- **Auto-fills client details.** The same pass reads the subscriber name, supply
  address, city and connection power off the bills and pre-fills the client form,
  which the operator can review and correct before generating.
- **Optional site report.** Drop in a technical / feasibility report and the
  engine reads the exact GPS, transformer & diesel ratings, module count and the
  per-building roof breakdown — adding a "Technical feasibility" slide (verdict,
  per-building power table, engineer validation) and using the precise location
  for the map and PVGIS.
- **Sizes the system sensibly.** Recommended capacity = `min(roof, grid
  connection, daytime consumption)` — so the proposal is technically adequate,
  not just optimistic.
- **Geolocation -> roof -> capacity.** Enter a roof area (or measure it on Google
  Maps); the engine converts it to a panel count and installable kWc, and can
  show a satellite preview of the site. With the **Google Solar API** it reads the
  building's real roof area and max panel count automatically (falls back to the
  area estimate where there's no coverage).
- **Two savings scenarios** (per the brief): grid replacement, and 50% grid /
  50% diesel — with monthly, annual and 15-year cumulative savings, plus a chart.
- **Location-accurate production.** With GPS coordinates the engine pulls the
  site's real specific yield (kWh/kWc/yr) from **PVGIS** (EU, free, key-less,
  global coverage) instead of a flat 1450 — so production and savings reflect the
  actual location, not an average. Falls back to the config default offline.
- **CFO-grade financials.** NPV of savings (with CIE tariff escalation), % of
  consumption covered, a 15-year cashflow table, and CO2 avoided — on a dedicated
  Financial analysis slide.
- **One-click cover email.** A ready-to-send client email (FR/EN), written by
  Claude when a key is present, with a deterministic template fallback.
- **Two output styles.**
  - **Aspan template** (default): opens Aspan's *actual* `.pptx` and fills in only
    the client's numbers — identical design, logo, icons and company copy, just
    new figures. For the NESKAO sample the output matches Aspan's original deck.
  - **Extended**: a generated deck in the same visual identity that *adds* the
    assignment's Executive summary and Current energy situation, plus the
    Technical feasibility and CFO financial-analysis slides.
- **Production-minded.** All engineering & economic assumptions live in
  `config/assumptions.yaml` — an operator tunes them without touching code.

---

## Quick start

```bash
# 1. Install dependencies (Python 3.10+)
pip install -r requirements.txt
#    PDF rendering uses PyMuPDF (installed above) — no system tools needed.

# 2. (Optional) add API keys for real bill extraction / maps
cp .env.example .env   # then edit; without keys it runs in MOCK mode

# 3a. Run the web app
streamlit run app.py

# 3b. ...or generate headless from the command line
python cli.py --bills "../Utility bills" --name NESKAO --roof 5149 \
              --grid 1250 --lang fr --ref OF-NESKAO-2026-001 --out proposal.pptx

# 4. Regenerate the sample NESKAO proposal
python make_sample.py fr
python make_sample.py en
```

> **No API key?** The engine never fabricates numbers. Without a key it runs in
> **manual mode**: uploaded bills produce empty rows you fill in by hand. Add
> `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) to extract bills automatically.

---

## Using the web app

1. **Upload bills** (PDF / image, several months) and click **Extract**. Review
   and edit the extracted table — low-confidence rows are flagged.
2. **Client information** — name, industry, grid kVA, diesel, roof area, and an
   address / GPS for the map preview. Sensible defaults are pre-filled.
3. **Recommendation** — see the recommended kWc, production and savings, then
   click **Generate proposal** to download the `.pptx`.

---

## Configuration

`config/assumptions.yaml` — every number that drives the proposal:
specific yield (1450 kWh/kWc for Abidjan), module power/area & packing factor,
CIE day tariff, PPA discount (−20%), diesel cost (700 FCFA/l × 0.30 l/kWh),
daytime consumption share, scenario splits, 15-year horizon.

`config/branding.yaml` — colors, fonts and all bilingual (FR/EN) slide copy.

Because the logic is config-driven, the same engine works for any client and
can be re-tuned by an operator in minutes.

---

## How the numbers are calculated

- **PV sizing:** `recommended = min(cap_roof, cap_grid, cap_daytime)`
  - `cap_roof`   = panels that fit on the usable roof × module power
  - `cap_grid`   = grid kVA × safety ratio (avoid reverse power into the grid)
  - `cap_daytime`= annual daytime consumption ÷ specific yield (no over-production)
- **Production:** `kWc × 1450 kWh/kWc` (Abidjan specific yield).
- **Solar offsets daytime consumption only**, so usable solar is capped at the
  annual daytime load.
- **Solar tariff:** `CIE_day × (1 − 0.20)` = a guaranteed 20% discount.
- **Scenario 1 (grid):** saving/kWh = `CIE_day − solar_tariff`.
- **Scenario 2 (grid+diesel):** baseline = `0.5×CIE_day + 0.5×diesel_cost`;
  saving/kWh = `baseline − solar_tariff`.
- Monthly = annual ÷ 12; 15-year = annual × 15.

**Calibration:** with the real NESKAO inputs the engine reproduces Aspan's actual
offer — 600 kWc, ~1000 modules, 870,000 kWh/yr, 67.10 FCFA/kWh solar tariff,
~14.6 M FCFA/yr and ~219 M FCFA over 15 years (Scenario 1). See `tests/`.

---

## Project structure

```
aspan_proposal_engine/
├── app.py                 # Streamlit web app
├── cli.py                 # Headless CLI
├── make_sample.py         # Regenerate the NESKAO sample proposal
├── config/
│   ├── assumptions.yaml   # Engineering & economic assumptions (operator-tunable)
│   ├── branding.yaml      # Colors, fonts, bilingual copy
│   └── client.example.json
├── aspan/
│   ├── config.py          # Load YAML config
│   ├── schema.py          # Typed data model (dataclasses)
│   ├── extraction.py      # Vision LLM bill extraction + mock fallback
│   ├── geo.py             # Roof -> panels -> kWc; Google Maps + Solar API
│   ├── engine.py          # Sizing + production + savings + financials
│   ├── email_gen.py       # Cover email (Claude or template)
│   ├── format_utils.py    # Money/number formatting (FR/EN)
│   └── pptx_builder.py     # Branded, bilingual .pptx renderer
├── tests/test_engine.py   # Calibration tests vs the real NESKAO offer
├── samples/               # Generated sample proposals (FR + EN)
├── requirements.txt
└── .env.example
```

---

## Testing

```bash
python tests/test_engine.py        # or: python -m pytest -q
```

The tests assert the engine reproduces the real Aspan NESKAO figures, so any
change to the assumptions that would break the calibration is caught.

---

## Notes & assumptions

- Designed for the CIE / Côte d'Ivoire context (FCFA, French bills) but fully
  parameterised; tariffs, yield and currency labels are in config.
- Vision extraction reads the first pages of each bill; always review the table
  before sending a proposal to a client — the app flags low-confidence values.
- Outputs are standard `.pptx`, editable in PowerPoint, Keynote or Google Slides.

© Work product for Aspan Energy (per the assessment brief).
