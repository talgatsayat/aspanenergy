"""Geolocation & roof-area helpers.

Recruiter brief: "take the geolocation (Google Maps), say the client is located
here, has this roof area, X panels fit on it, therefore we can install Y kWc so
the system is adequately sized."

This module turns a roof area (in m2) into a panel count and an installable
capacity, and — when a Google Maps API key is available — can geocode an address
and build a Static Map preview of the site. Everything degrades gracefully
without a key: the operator can simply type the roof area by hand.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

from .config import assumptions


@dataclass
class RoofEstimate:
    roof_area_m2: float
    usable_panel_area_m2: float
    n_panels: int
    capacity_kwc: float
    module_power_wp: int
    module_area_m2: float


def panels_from_area(roof_area_m2: float) -> RoofEstimate:
    """Roof area -> how many modules fit -> installable kWc.

    usable_area = roof_area / packing_factor        (spacing, setbacks, walkways)
    n_panels    = floor(usable_area / module_area)
    kWc         = n_panels * module_power / 1000
    """
    s = assumptions()["solar"]
    packing = s["packing_factor"]
    module_area = s["module_area_m2"]
    module_wp = s["module_power_wp"]

    usable = roof_area_m2 / packing
    n_panels = int(usable // module_area)
    capacity = round(n_panels * module_wp / 1000, 1)
    return RoofEstimate(
        roof_area_m2=roof_area_m2,
        usable_panel_area_m2=round(usable, 1),
        n_panels=n_panels,
        capacity_kwc=capacity,
        module_power_wp=module_wp,
        module_area_m2=module_area,
    )


def parse_dms(dms: str) -> Optional[float]:
    """Parse a DMS coordinate like 5°15'53.8\"N into decimal degrees.

    Useful because Aspan technical reports store GPS as DMS
    (e.g. NESKAO: 5°15'53.8\"N 4°00'12.4\"W).
    """
    m = re.match(
        r"""\s*(\d+)[°:\s]+(\d+)['\s]+([\d.]+)["\s]*([NSEW])""",
        dms.strip(),
        re.IGNORECASE,
    )
    if not m:
        return None
    deg, minutes, sec, hemi = m.groups()
    val = float(deg) + float(minutes) / 60 + float(sec) / 3600
    if hemi.upper() in ("S", "W"):
        val = -val
    return round(val, 6)


def parse_latlng(text: str) -> Optional[Tuple[float, float]]:
    """Parse 'lat,lng' OR a DMS pair 'D°M'S\"N D°M'S\"W'."""
    if not text:
        return None
    # decimal "5.264, -4.003"
    m = re.match(r"\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*$", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    # DMS pair
    parts = re.findall(r"\d+[°:\s]+\d+['\s]+[\d.]+[\"\s]*[NSEW]", text)
    if len(parts) == 2:
        lat = parse_dms(parts[0])
        lng = parse_dms(parts[1])
        if lat is not None and lng is not None:
            return lat, lng
    return None


def _api_key() -> Optional[str]:
    return os.getenv("GOOGLE_MAPS_API_KEY")


def geocode(address: str) -> Optional[Tuple[float, float]]:
    """Address -> (lat, lng).

    Uses Google Geocoding when GOOGLE_MAPS_API_KEY is set, otherwise falls back
    to OpenStreetMap Nominatim, which is free and key-less. This means an address
    read off a bill can be turned into coordinates (and thus a PVGIS yield and a
    map) without any API key.
    """
    if not address:
        return None
    key = _api_key()
    if key:
        try:
            r = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": address, "key": key},
                timeout=10,
            )
            data = r.json()
            if data.get("status") == "OK":
                loc = data["results"][0]["geometry"]["location"]
                return loc["lat"], loc["lng"]
        except Exception:
            pass
    # Key-less fallback: OpenStreetMap Nominatim
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": "AspanProposalEngine/1.0"},
            timeout=10,
        )
        arr = r.json()
        if arr:
            return float(arr[0]["lat"]), float(arr[0]["lon"])
    except Exception:
        return None
    return None


def static_map_url(lat: float, lng: float, zoom: int = 18, size: str = "640x360") -> Optional[str]:
    """Build a Google Static Maps satellite URL for the site (needs API key)."""
    key = _api_key()
    if not key:
        return None
    return (
        "https://maps.googleapis.com/maps/api/staticmap"
        f"?center={lat},{lng}&zoom={zoom}&size={size}&maptype=satellite"
        f"&markers=color:0xF2A900%7C{lat},{lng}&key={key}"
    )


def maps_link(lat: float, lng: float) -> str:
    """Plain Google Maps link (no key required) for the operator to inspect."""
    return f"https://www.google.com/maps/@{lat},{lng},18z"


def google_maps_embed_url(lat: float, lng: float, zoom: int = 16) -> str:
    """Interactive Google Maps embed URL (no API key required).

    Uses the classic ``output=embed`` form so the map can be shown inline in an
    iframe without a Google Maps API key.
    """
    return f"https://maps.google.com/maps?q={lat},{lng}&z={zoom}&output=embed"


@dataclass
class PVGISYield:
    """Location-specific PV yield from the EU PVGIS service (free, no key)."""

    specific_yield_kwh_per_kwc: float   # kWh produced per kWc per year, this site
    optimal_tilt: Optional[float]
    optimal_azimuth: Optional[float]
    system_loss_pct: float
    source: str = "PVGIS"


def pvgis_specific_yield(lat: float, lon: float, loss: float = 14.0,
                         mounting: str = "building") -> Optional[PVGISYield]:
    """Real annual specific yield (kWh/kWc/yr) at a location, via PVGIS.

    PVGIS (EU Joint Research Centre) is free and key-less, with global coverage
    including West Africa. We request a 1 kWp system at the optimal tilt, so the
    returned annual energy *is* the specific yield. Replaces the flat 1450
    assumption with a value calibrated to the actual site irradiance.

    Returns None on any failure so the caller falls back to the config default.
    """
    try:
        r = requests.get(
            "https://re.jrc.ec.europa.eu/api/v5_2/PVcalc",
            params={
                "lat": lat, "lon": lon, "peakpower": 1, "loss": loss,
                "mountingplace": mounting, "pvtechchoice": "crystSi",
                "optimalangles": 1, "outputformat": "json",
            },
            timeout=20,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        fixed = data["outputs"]["totals"]["fixed"]
        e_y = float(fixed["E_y"])  # annual kWh for 1 kWp == specific yield
        inputs = data.get("inputs", {}).get("mounting_system", {}).get("fixed", {})
        slope = inputs.get("slope", {}).get("value")
        azimuth = inputs.get("azimuth", {}).get("value")
        if e_y <= 0:
            return None
        return PVGISYield(
            specific_yield_kwh_per_kwc=round(e_y, 0),
            optimal_tilt=slope,
            optimal_azimuth=azimuth,
            system_loss_pct=loss,
        )
    except Exception:
        return None


@dataclass
class SolarInsights:
    """Roof analysis from the Google Solar API (Building Insights)."""

    roof_area_m2: float          # usable/whole roof area detected
    max_panels: int              # max panels that physically fit (Google)
    google_capacity_kwc: float   # capacity from Google's panel model
    sunshine_hours: Optional[float]
    source: str = "google_solar"


def google_solar_insights(lat: float, lng: float) -> Optional[SolarInsights]:
    """Query the Google Solar API for real roof geometry at this location.

    This is exactly the recruiter's ask: "take the geolocation, say the roof
    area, how many panels fit, therefore the capacity we can install."

    Needs GOOGLE_MAPS_API_KEY with the *Solar API* enabled. Coverage is
    growing but not universal; returns None where the building is not covered,
    so the caller can fall back to the area-based estimate.
    """
    key = _api_key()
    if not key:
        return None
    try:
        r = requests.get(
            "https://solar.googleapis.com/v1/buildingInsights:findClosest",
            params={"location.latitude": lat, "location.longitude": lng,
                    "requiredQuality": "LOW", "key": key},
            timeout=12,
        )
        if r.status_code != 200:
            return None
        sp = r.json().get("solarPotential", {})
        if not sp:
            return None
        panel_w = sp.get("panelCapacityWatts", 250)
        max_panels = int(sp.get("maxArrayPanelsCount", 0))
        roof_area = float(
            sp.get("wholeRoofStats", {}).get("areaMeters2")
            or sp.get("maxArrayAreaMeters2", 0)
        )
        if max_panels == 0 or roof_area == 0:
            return None
        return SolarInsights(
            roof_area_m2=round(roof_area, 0),
            max_panels=max_panels,
            google_capacity_kwc=round(max_panels * panel_w / 1000, 1),
            sunshine_hours=sp.get("maxSunshineHoursPerYear"),
        )
    except Exception:
        return None
