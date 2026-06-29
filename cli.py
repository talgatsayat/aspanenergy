"""Headless CLI — generate a proposal without the web UI.

Examples:
  # Folder of bills + client JSON
  python cli.py --bills "../Utility bills" --client client.json --out proposal.pptx

  # Minimal (uses defaults + mock extraction)
  python cli.py --bills "../Utility bills" --name NESKAO --roof 5149 --lang fr

A client JSON may contain any ClientInfo field, e.g.:
  {"name": "NESKAO", "grid_capacity_kva": 1250, "roof_area_m2": 5149,
   "has_diesel": true, "language": "fr"}
"""
from __future__ import annotations

import argparse
import glob
import json
import os

from aspan.engine import build_proposal_data
from aspan.extraction import extract_bills
from aspan.pptx_builder import build_pptx
from aspan.schema import ClientInfo


def main():
    ap = argparse.ArgumentParser(description="Aspan Proposal Engine (CLI)")
    ap.add_argument("--bills", required=True, help="Folder or glob of bill files")
    ap.add_argument("--client", help="Path to client JSON")
    ap.add_argument("--name", default="Client")
    ap.add_argument("--roof", type=float, default=None, help="Roof area m2")
    ap.add_argument("--grid", type=float, default=0.0,
                    help="Grid kVA (0 = unknown, no grid-capacity limit)")
    ap.add_argument("--lang", default="fr", choices=["fr", "en"])
    ap.add_argument("--ref", default="OF-PROPOSAL-001")
    ap.add_argument("--pvgis", action="store_true",
                    help="Fetch site-specific yield from PVGIS (needs client lat/lng)")
    ap.add_argument("--report", help="Technical/feasibility site report (PDF) to "
                    "add a feasibility slide + exact GPS")
    ap.add_argument("--style", choices=["template", "extended"], default="template",
                    help="template = Aspan's exact deck; extended = + summary/feasibility/financials")
    ap.add_argument("--out", default="proposal.pptx")
    args = ap.parse_args()

    # Resolve bills
    if os.path.isdir(args.bills):
        files = sorted(glob.glob(os.path.join(args.bills, "*")))
    else:
        files = sorted(glob.glob(args.bills))
    files = [f for f in files if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))]
    if not files:
        ap.error(f"No bill files found at {args.bills}")

    # Build client
    if args.client:
        with open(args.client, encoding="utf-8") as fh:
            client = ClientInfo(**json.load(fh))
    else:
        client = ClientInfo(name=args.name, grid_capacity_kva=args.grid,
                            roof_area_m2=args.roof, language=args.lang)

    print(f"Extracting {len(files)} bill(s)...")
    res = extract_bills(files)
    print(f"  mode: {res.mode}")
    for w in res.warnings:
        print(f"  ! {w}")

    if sum(b.total_kwh for b in res.bills) <= 0:
        ap.error(
            "No consumption could be extracted (no API key, or extraction failed). "
            "Set ANTHROPIC_API_KEY / OPENAI_API_KEY, or provide bills the model can "
            "read. The engine does not fabricate figures.")

    specific_yield, yield_source = None, "default"
    if args.pvgis and client.latitude is not None and client.longitude is not None:
        from aspan.geo import pvgis_specific_yield
        py = pvgis_specific_yield(client.latitude, client.longitude)
        if py:
            specific_yield = py.specific_yield_kwh_per_kwc
            yield_source = f"PVGIS @ {client.latitude:.3f},{client.longitude:.3f}"
            print(f"  PVGIS yield: {specific_yield:.0f} kWh/kWc")
        else:
            print("  PVGIS unavailable; using default yield.")

    site_report = None
    if args.report:
        from aspan.extraction import extract_report
        site_report, rwarn = extract_report(args.report)
        for w in rwarn:
            print(f"  ! {w}")
        if site_report:
            print(f"  Site report: GPS {site_report.latitude}, {site_report.longitude}; "
                  f"{len(site_report.buildings)} buildings")

    data = build_proposal_data(client, res.bills, reference=args.ref,
                               specific_yield=specific_yield, yield_source=yield_source,
                               site_report=site_report)
    sz, (s1, s2) = data.sizing, data.scenarios
    print(f"Recommended: {sz.recommended_kwc:.0f} kWc ({sz.n_panels} panels), "
          f"{sz.annual_production_kwh:,.0f} kWh/yr, limited by {sz.binding_constraint}")
    print(f"Scenario 1: {s1.annual_saving:,.0f}/yr  |  15yr {s1.cumulative_15y_saving:,.0f}")
    print(f"Scenario 2: {s2.annual_saving:,.0f}/yr  |  15yr {s2.cumulative_15y_saving:,.0f}")

    if args.style == "template":
        from aspan.template_builder import build_from_template
        city = getattr(client, "country", None) or "Abidjan"
        path = build_from_template(data, args.out, city="Abidjan")
    else:
        path = build_pptx(data, args.out)
    print(f"Saved proposal ({args.style}): {path}")


if __name__ == "__main__":
    main()
