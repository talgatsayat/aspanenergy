# Architecture

The engine is a small, layered pipeline. Each layer has one job and a clean
data contract (the dataclasses in `aspan/schema.py`), so any stage can be
swapped or tested in isolation.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          INTERFACES                                        │
│   app.py (Streamlit UI)              cli.py (headless)     make_sample.py   │
└───────────────┬───────────────────────────┬──────────────────┬────────────┘
                │                           │                  │
                ▼                           ▼                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATION                                       │
│            engine.build_proposal_data(client, bills)                       │
└───────┬───────────────────┬───────────────────┬──────────────────────────┘
        │                   │                   │
        ▼                   ▼                   ▼
┌───────────────┐  ┌──────────────────┐  ┌───────────────────────────────┐
│  EXTRACTION   │  │   GEO / SIZING   │  │        CALCULATION            │
│ extraction.py │  │     geo.py       │  │         engine.py             │
│               │  │                  │  │                               │
│ bills ─▶ kWh, │  │ roof m² ─▶ panels│  │ summarize_bills               │
│ tariffs, cost │  │  ─▶ kWc          │  │ size_system  (min constraint) │
│ (vision LLM   │  │ geocode / static │  │ compute_scenarios (S1, S2)    │
│  or MOCK)     │  │ map (Google)     │  │                               │
└──────┬────────┘  └────────┬─────────┘  └──────────────┬────────────────┘
       │                    │                           │
       └──────────── ProposalData (schema.py) ──────────┘
                                │
                                ▼
                  ┌───────────────────────────────┐
                  │          RENDER               │
                  │       pptx_builder.py         │
                  │  branded, bilingual .pptx     │
                  │  (10 slides + native chart)   │
                  └───────────────────────────────┘

        config/assumptions.yaml  +  config/branding.yaml
        (read by every layer; the single source of truth)
```

## Data contract (`aspan/schema.py`)

- `MonthlyBill` — one bill: `total_kwh`, `day_kwh`, `night_kwh`, `total_cost`,
  `day_tariff`, `confidence`.
- `ClientInfo` — site facts: industry, country, `grid_capacity_kva`,
  `roof_area_m2`, lat/lng, `has_diesel`, `language`.
- `BillSummary`, `SizingResult`, `ScenarioResult` — computed results.
- `ProposalData` — the full bundle the renderer consumes.

## Layer responsibilities

| Layer | File | Input → Output |
|-------|------|----------------|
| Extraction | `extraction.py` | bill files → `List[MonthlyBill]` + client identity; optional site report → `SiteReport` (vision LLM) |
| Geo | `geo.py` | roof m² → panels/kWc; address → lat/lng; Google Solar roof analysis; PVGIS site yield; satellite URL |
| Sizing | `engine.size_system` | client + summary → `SizingResult` (min of 3 caps) |
| Savings | `engine.compute_scenarios` | sizing + summary → `[ScenarioResult]` |
| Financials | `engine.compute_financials` | → `FinancialSummary` (NPV, coverage, cashflow, CO₂) |
| Email | `email_gen.py` | `ProposalData` → cover email (Claude or template) |
| Render | `pptx_builder.py` | `ProposalData` → `.pptx` |

## Design choices

- **Config over code.** Tariffs, yield, diesel cost, discount, scenario splits
  and all slide copy live in YAML so a non-developer operator can adapt it.
- **Graceful degradation.** No API key → MOCK extraction; no Maps key → manual
  roof area. The pipeline never hard-fails on one bad bill.
- **Calibrated, not invented.** Defaults are derived from Aspan's real NESKAO
  offer and technical report, and locked in by `tests/test_engine.py`.
- **Dependency-light core.** The engine uses only the standard library + PyYAML;
  vision/UI/maps are optional extras.

## Extending it

- New tariff structure / country → edit `config/assumptions.yaml`.
- New slide or rebrand → `config/branding.yaml` + `pptx_builder.py`.
- New bill format → the extraction prompt in `extraction.py` is schema-driven.
- New scenario → add a block under `scenarios:` and one loop iteration.
