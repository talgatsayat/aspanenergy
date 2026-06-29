"""Typed data model for the proposal pipeline (stdlib dataclasses).

These structures are the contract between the extraction layer, the
calculation engine and the pptx renderer. Each bill carries an optional
``confidence`` flag so the operator can review low-confidence OCR fields
before a financial document goes to a client.

Dataclasses (stdlib) are used instead of a 3rd-party validation library to
keep the core engine dependency-free and runnable anywhere.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class MonthlyBill:
    """One utility bill (one month) extracted from a CIE invoice."""

    period: str                      # e.g. "2025.06"
    total_kwh: float                 # total active energy (kWh)
    day_kwh: Optional[float] = None  # daytime energy (Jour + Pointe), kWh
    night_kwh: Optional[float] = None
    total_cost: Optional[float] = None
    day_tariff: Optional[float] = None
    confidence: float = 1.0          # 0..1 (1 = manual / verified)

    def __post_init__(self):
        if self.total_kwh < 0:
            raise ValueError(f"{self.period}: total_kwh must be >= 0")
        self.confidence = max(0.0, min(1.0, self.confidence))

    @property
    def derived_day_kwh(self) -> float:
        """Daytime kWh, falling back to the configured daytime share."""
        if self.day_kwh is not None:
            return self.day_kwh
        from .config import assumptions

        return self.total_kwh * assumptions()["consumption"]["daytime_share"]


@dataclass
class ClientInfo:
    """Customer-supplied (or assumed) facility information."""

    name: str = "Client"
    industry: str = "Industrial"
    country: str = "Cote d'Ivoire"
    description: str = ""
    has_diesel: bool = False
    grid_capacity_kva: float = 0.0   # 0 = unknown (no grid-capacity limit)
    roof_area_m2: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    language: str = "fr"  # "fr" or "en"


@dataclass
class BillSummary:
    months_count: int
    avg_monthly_kwh: float
    annual_kwh: float
    annual_day_kwh: float
    annual_night_kwh: float
    avg_day_tariff: float
    annual_cost_estimate: float
    lowest_confidence: float


@dataclass
class SizingResult:
    recommended_kwc: float
    n_panels: int
    cap_by_roof_kwc: float
    cap_by_grid_kwc: float
    cap_by_consumption_kwc: float
    binding_constraint: str
    roof_area_m2: Optional[float]
    annual_production_kwh: float
    specific_yield: float = 1450.0
    yield_source: str = "default"


@dataclass
class ScenarioResult:
    key: str
    name_en: str
    name_fr: str
    baseline_cost_per_kwh: float
    solar_tariff_per_kwh: float
    saving_per_kwh: float
    solar_kwh_used: float
    monthly_saving: float
    annual_saving: float
    cumulative_15y_saving: float
    saving_pct: float


@dataclass
class BuildingRoof:
    """One building's roof allocation, from the technical site report."""

    name: str
    area_m2: Optional[float] = None
    capacity_kwc: Optional[float] = None


@dataclass
class SiteReport:
    """Structured data extracted from a technical feasibility / site-visit report."""

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    transformer_kva: Optional[float] = None
    diesel_kw: Optional[float] = None
    recommended_kwc: Optional[float] = None
    n_modules: Optional[int] = None
    buildings: List[BuildingRoof] = field(default_factory=list)
    feasibility_verdict: Optional[str] = None
    engineer_name: Optional[str] = None
    client_name: Optional[str] = None
    client_city: Optional[str] = None
    client_country: Optional[str] = None
    audit_done: bool = True   # a report implies the site visit was performed
    confidence: float = 1.0


@dataclass
class CashflowYear:
    year: int
    annual_saving: float
    cumulative_saving: float


@dataclass
class FinancialSummary:
    """CFO-oriented metrics built on the headline (grid replacement) scenario."""

    solar_coverage_pct: float        # solar kWh / total consumption
    day_coverage_pct: float          # solar kWh / daytime consumption
    current_annual_cost: float       # what the replaced energy costs today
    solar_annual_cost: float         # what it costs under the PPA
    annual_saving: float
    discount_rate: float
    npv_savings: float               # NPV, flat tariff
    npv_savings_escalated: float     # NPV with projected CIE escalation
    tariff_escalation: float
    cashflow: List[CashflowYear]
    co2_tonnes_per_year: float
    co2_tonnes_horizon: float


@dataclass
class ProposalData:
    """Everything the renderer needs to build the deck."""

    client: ClientInfo
    bill_summary: BillSummary
    sizing: SizingResult
    scenarios: List[ScenarioResult]
    financial: FinancialSummary
    solar_tariff: float
    cie_day_tariff: float
    discount_pct: int
    contract_years: int
    reference: str = "OF-PROPOSAL-001"
    site_report: Optional[SiteReport] = None

    def to_dict(self) -> dict:
        return asdict(self)
