"""Core calculation engine: bill aggregation, PV sizing, production & savings.

Design goals: transparent, configurable (everything from assumptions.yaml),
and calibrated against the real Aspan NESKAO 600 kWc offer so the numbers a
client sees match what Aspan engineers would produce by hand.
"""
from __future__ import annotations

from typing import List

from .config import assumptions
from .geo import panels_from_area
from .schema import (
    BillSummary,
    CashflowYear,
    ClientInfo,
    FinancialSummary,
    MonthlyBill,
    ProposalData,
    ScenarioResult,
    SizingResult,
)


# --------------------------------------------------------------------------
# 1. Aggregate the bills into an annual view
# --------------------------------------------------------------------------
def summarize_bills(bills: List[MonthlyBill]) -> BillSummary:
    if not bills:
        raise ValueError("At least one bill is required.")
    if sum(b.total_kwh for b in bills) <= 0:
        raise ValueError(
            "Total consumption is zero - no real bill data. The engine does not "
            "fabricate figures; provide readable bills or enter the values manually.")

    a = assumptions()
    day_share = a["consumption"]["daytime_share"]
    default_tariff = a["tariffs"]["cie_day_tariff"]

    n = len(bills)
    total_kwh = sum(b.total_kwh for b in bills)
    avg_monthly = total_kwh / n
    annual_kwh = avg_monthly * 12

    day_kwh_sum = sum(b.derived_day_kwh for b in bills)
    annual_day = (day_kwh_sum / n) * 12
    annual_night = annual_kwh - annual_day

    tariffs = [b.day_tariff for b in bills if b.day_tariff]
    avg_tariff = sum(tariffs) / len(tariffs) if tariffs else default_tariff

    costs = [b.total_cost for b in bills if b.total_cost]
    if costs:
        annual_cost = (sum(costs) / len(costs)) * 12
    else:
        annual_cost = annual_kwh * avg_tariff  # rough estimate

    return BillSummary(
        months_count=n,
        avg_monthly_kwh=round(avg_monthly, 1),
        annual_kwh=round(annual_kwh, 1),
        annual_day_kwh=round(annual_day, 1),
        annual_night_kwh=round(annual_night, 1),
        avg_day_tariff=round(avg_tariff, 2),
        annual_cost_estimate=round(annual_cost, 0),
        lowest_confidence=min(b.confidence for b in bills),
    )


# --------------------------------------------------------------------------
# 2. PV system sizing
#    recommended = min(roof limit, grid limit, consumption limit)
# --------------------------------------------------------------------------
def size_system(client: ClientInfo, summary: BillSummary,
                specific_yield: float = None, yield_source: str = "default") -> SizingResult:
    a = assumptions()
    s = a["solar"]
    yield_kwc = specific_yield or s["yield_kwh_per_kwc"]
    round_to = a["sizing"]["round_to_kwc"]
    grid_ratio = a["sizing"]["max_pv_to_grid_ratio"]

    # Consumption limit: produce no more than annual daytime consumption
    # (PV offsets daytime load only; avoid export / curtailment).
    cap_consumption = summary.annual_day_kwh / yield_kwc

    # Grid limit: stay below a safe share of the connection (avoid reverse power).
    # 0 / unknown grid capacity => no grid constraint applied.
    cap_grid = (client.grid_capacity_kva * grid_ratio
                if client.grid_capacity_kva and client.grid_capacity_kva > 0
                else float("inf"))

    # Roof limit: how much physically fits (panels packed on usable roof).
    if client.roof_area_m2:
        cap_roof = panels_from_area(client.roof_area_m2).capacity_kwc
    else:
        cap_roof = float("inf")

    caps = {
        "roof": cap_roof,
        "grid": cap_grid,
        "daytime consumption": cap_consumption,
    }
    binding = min(caps, key=caps.get)
    raw_kwc = caps[binding]

    # Round to the nearest clean figure (e.g. nearest 10 kWc).
    recommended = max(round_to, int(round(raw_kwc / round_to)) * round_to)

    # Panel count consistent with the rounded recommended capacity.
    n_panels = int(round(recommended * 1000 / s["module_power_wp"]))

    annual_production = recommended * yield_kwc

    return SizingResult(
        recommended_kwc=float(recommended),
        n_panels=n_panels,
        cap_by_roof_kwc=round(cap_roof, 1) if cap_roof != float("inf") else -1,
        cap_by_grid_kwc=round(cap_grid, 1) if cap_grid != float("inf") else -1,
        cap_by_consumption_kwc=round(cap_consumption, 1),
        binding_constraint=binding,
        roof_area_m2=client.roof_area_m2,
        annual_production_kwh=round(annual_production, 0),
        specific_yield=round(yield_kwc, 0),
        yield_source=yield_source,
    )


# --------------------------------------------------------------------------
# 3. Savings scenarios
# --------------------------------------------------------------------------
def _day_tariff(summary: BillSummary) -> float:
    """The client's actual CIE day tariff (from their bills), config as fallback."""
    if summary and summary.avg_day_tariff:
        return summary.avg_day_tariff
    return assumptions()["tariffs"]["cie_day_tariff"]


def _solar_tariff(summary: BillSummary = None) -> float:
    a = assumptions()
    return round(_day_tariff(summary) * (1 - a["ppa"]["discount_vs_grid"]), 2)


def _diesel_cost_per_kwh() -> float:
    d = assumptions()["diesel"]
    return d["fuel_price_fcfa_per_litre"] * d["litres_per_kwh"]


def compute_scenarios(sizing: SizingResult, summary: BillSummary) -> List[ScenarioResult]:
    a = assumptions()
    horizon = a["scenarios"]["horizon_years"]
    day_tariff = _day_tariff(summary)
    diesel_cost = _diesel_cost_per_kwh()
    solar_tariff = _solar_tariff(summary)

    # Solar offsets daytime consumption only -> cap usable solar at daytime load.
    solar_used = min(sizing.annual_production_kwh, summary.annual_day_kwh)

    results: List[ScenarioResult] = []
    for key in ("scenario_1", "scenario_2"):
        sc = a["scenarios"][key]
        baseline = sc["grid_share"] * day_tariff + sc["diesel_share"] * diesel_cost
        saving_per_kwh = baseline - solar_tariff
        annual = solar_used * saving_per_kwh
        results.append(
            ScenarioResult(
                key=key,
                name_en=sc["name_en"],
                name_fr=sc["name_fr"],
                baseline_cost_per_kwh=round(baseline, 2),
                solar_tariff_per_kwh=solar_tariff,
                saving_per_kwh=round(saving_per_kwh, 2),
                solar_kwh_used=round(solar_used, 0),
                monthly_saving=round(annual / 12, 0),
                annual_saving=round(annual, 0),
                cumulative_15y_saving=round(annual * horizon, 0),
                saving_pct=round(saving_per_kwh / baseline * 100, 1),
            )
        )
    return results


# --------------------------------------------------------------------------
# 4. Financial analysis (CFO view) — built on Scenario 1 (grid replacement)
# --------------------------------------------------------------------------
def compute_financials(
    sizing: SizingResult, summary: BillSummary, scenarios: List[ScenarioResult]
) -> FinancialSummary:
    a = assumptions()
    fin = a["finance"]
    horizon = a["scenarios"]["horizon_years"]
    r = fin["discount_rate"]
    esc = fin["projection_tariff_escalation"]
    co2 = fin["co2_kg_per_kwh"]

    s1 = scenarios[0]
    solar_used = s1.solar_kwh_used
    current_cost = solar_used * s1.baseline_cost_per_kwh
    solar_cost = solar_used * s1.solar_tariff_per_kwh
    annual_saving = s1.annual_saving

    # NPV (flat) and NPV with CIE tariff escalation (savings grow over time).
    npv_flat = sum(annual_saving / (1 + r) ** y for y in range(1, horizon + 1))
    npv_esc = sum(
        annual_saving * (1 + esc) ** (y - 1) / (1 + r) ** y
        for y in range(1, horizon + 1)
    )

    cashflow: List[CashflowYear] = []
    cumulative = 0.0
    for y in range(1, horizon + 1):
        yearly = annual_saving * (1 + esc) ** (y - 1)
        cumulative += yearly
        cashflow.append(CashflowYear(
            year=y, annual_saving=round(yearly, 0), cumulative_saving=round(cumulative, 0)))

    return FinancialSummary(
        solar_coverage_pct=round(solar_used / summary.annual_kwh * 100, 1),
        day_coverage_pct=round(solar_used / summary.annual_day_kwh * 100, 1),
        current_annual_cost=round(current_cost, 0),
        solar_annual_cost=round(solar_cost, 0),
        annual_saving=round(annual_saving, 0),
        discount_rate=r,
        npv_savings=round(npv_flat, 0),
        npv_savings_escalated=round(npv_esc, 0),
        tariff_escalation=esc,
        cashflow=cashflow,
        co2_tonnes_per_year=round(solar_used * co2 / 1000, 1),
        co2_tonnes_horizon=round(solar_used * co2 / 1000 * horizon, 1),
    )


# --------------------------------------------------------------------------
# 5. End-to-end orchestration
# --------------------------------------------------------------------------
def build_proposal_data(
    client: ClientInfo, bills: List[MonthlyBill], reference: str = "OF-PROPOSAL-001",
    specific_yield: float = None, yield_source: str = "default",
    site_report=None,
) -> ProposalData:
    a = assumptions()
    summary = summarize_bills(bills)
    sizing = size_system(client, summary, specific_yield=specific_yield,
                         yield_source=yield_source)
    scenarios = compute_scenarios(sizing, summary)
    financial = compute_financials(sizing, summary, scenarios)

    return ProposalData(
        client=client,
        bill_summary=summary,
        sizing=sizing,
        scenarios=scenarios,
        financial=financial,
        site_report=site_report,
        solar_tariff=_solar_tariff(summary),
        cie_day_tariff=round(_day_tariff(summary), 2),
        discount_pct=int(a["ppa"]["discount_vs_grid"] * 100),
        contract_years=a["scenarios"]["horizon_years"],
        reference=reference,
    )
