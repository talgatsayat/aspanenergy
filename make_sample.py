"""Generate the sample NESKAO proposal (calibration deliverable).

Usage:  python make_sample.py [fr|en] [output.pptx]
"""
import sys

from aspan.engine import build_proposal_data
from aspan.pptx_builder import build_pptx
from aspan.template_builder import build_from_template
from aspan.schema import BuildingRoof, ClientInfo, MonthlyBill, SiteReport


def neskao_bills():
    # Representative monthly consumption from CIE bills (~256,800 kWh/month),
    # day tariff 83.87 FCFA/kWh confirmed on the invoices.
    return [
        MonthlyBill(period=f"2025.{m:02d}", total_kwh=256_816,
                    day_tariff=83.87, confidence=1.0)
        for m in range(1, 13)
    ]


def neskao_client(lang="fr"):
    return ClientInfo(
        name="NESKAO",
        industry="Agroalimentaire (cacao)" if lang == "fr" else "Food & beverage (cocoa)",
        country="Cote d'Ivoire",
        description="Production de produits a base de cacao et boissons instantanees.",
        has_diesel=True,
        grid_capacity_kva=1250,
        roof_area_m2=5149,
        latitude=5.2649, longitude=-4.0034,
        address="Zone Industrielle, Abidjan",
        language=lang,
    )


def neskao_report():
    # Values from the real NESKAO technical feasibility report.
    return SiteReport(
        latitude=5.2649, longitude=-4.0034,
        transformer_kva=1250, diesel_kw=1100,
        recommended_kwc=600, n_modules=1000,
        buildings=[
            BuildingRoof("Batiment T9", 2152, 320),
            BuildingRoof("Chambre Froide / R1", 768, 130),
            BuildingRoof("Nettoyage & Stockage", 829, 110),
            BuildingRoof("Torrefaction / Decort.", 1400, 40),
        ],
        feasibility_verdict=("Le projet de centrale solaire de 600 kWc pour NESKAO "
                             "est techniquement realisable et pertinent."),
        engineer_name="ADIOBY David",
    )


def main():
    lang = sys.argv[1] if len(sys.argv) > 1 else "fr"
    out = sys.argv[2] if len(sys.argv) > 2 else f"samples/Aspan_Proposal_NESKAO_{lang}.pptx"
    data = build_proposal_data(neskao_client(lang), neskao_bills(),
                               reference="OF-NESKAO-2026-001",
                               site_report=neskao_report())
    # Primary deliverable: Aspan's own deck, filled with this client's numbers.
    path = build_from_template(data, out, city="Abidjan")
    # Also keep the "extended" version (adds exec summary, feasibility, financial).
    ext = out.replace(".pptx", "_extended.pptx")
    build_pptx(data, ext)
    s1, s2 = data.scenarios
    print(f"Saved: {path}")
    print(f"  (extended version: {ext})")
    print(f"  Recommended: {data.sizing.recommended_kwc:.0f} kWc | "
          f"{data.sizing.n_panels} panels | "
          f"{data.sizing.annual_production_kwh:,.0f} kWh/yr")
    print(f"  Scenario 1: {s1.annual_saving:,.0f}/yr | 15yr {s1.cumulative_15y_saving:,.0f}")
    print(f"  Scenario 2: {s2.annual_saving:,.0f}/yr | 15yr {s2.cumulative_15y_saving:,.0f}")


if __name__ == "__main__":
    main()
