"""Generate a ready-to-send cover email for the proposal (FR / EN).

Two modes:
  - AI (Claude): a tailored, persuasive note written for the client's industry.
  - Template fallback: a clean deterministic email (always available, offline).

The email always carries the headline numbers from the analysis, so an Aspan
operator can attach the deck and send in one click.
"""
from __future__ import annotations

import os
from typing import Dict

from .config import branding
from .format_utils import fmt_int, fmt_money, t
from .schema import ProposalData


def _facts(data: ProposalData, lang: str) -> str:
    d = data
    s1 = d.scenarios[0]
    return (
        f"- system: {fmt_int(d.sizing.recommended_kwc)} kWc, "
        f"{fmt_int(d.sizing.annual_production_kwh)} kWh/yr\n"
        f"- discount: {d.discount_pct}% below CIE day tariff "
        f"({d.solar_tariff:.2f} vs {d.cie_day_tariff:.2f} FCFA/kWh)\n"
        f"- annual saving (grid replacement): {fmt_money(s1.annual_saving, lang)}\n"
        f"- {d.contract_years}-year cumulative: {fmt_money(s1.cumulative_15y_saving, lang)}\n"
        f"- NPV of savings: {fmt_money(d.financial.npv_savings_escalated, lang)}\n"
        f"- zero investment, zero O&M cost, pay only for solar kWh consumed"
    )


def _template(data: ProposalData, lang: str) -> Dict[str, str]:
    d = data
    s1 = d.scenarios[0]
    company = branding()["company"]
    if lang == "fr":
        subject = f"Proposition solaire {company['name']} pour {d.client.name} – économies garanties, zéro investissement"
        body = (
            f"Bonjour,\n\n"
            f"Suite à l'analyse de vos factures d'électricité, {company['name']} a le "
            f"plaisir de vous transmettre une proposition pour une centrale solaire de "
            f"{fmt_int(d.sizing.recommended_kwc)} kWc en autoconsommation sur votre site.\n\n"
            f"Les points clés :\n"
            f"  • Aucun investissement de votre part : nous finançons, installons et "
            f"exploitons l'installation.\n"
            f"  • Un tarif solaire garanti {d.discount_pct} % inférieur au tarif de jour CIE.\n"
            f"  • Une économie estimée de {fmt_money(s1.annual_saving, lang)} par an, "
            f"soit {fmt_money(s1.cumulative_15y_saving, lang)} sur {d.contract_years} ans.\n"
            f"  • {fmt_int(d.financial.co2_tonnes_per_year)} tonnes de CO2 évitées chaque année.\n\n"
            f"Vous trouverez le détail (situation actuelle, dimensionnement, modèle PPA et "
            f"analyse financière) dans la présentation jointe.\n\n"
            f"Nous restons à votre disposition pour échanger et organiser une visite technique.\n\n"
            f"Cordialement,\n"
            f"{company['contact_name']}\n{company['contact_role_fr']}\n"
            f"{company['contact_email']}"
        )
    else:
        subject = f"{company['name']} solar proposal for {d.client.name} - guaranteed savings, zero investment"
        body = (
            f"Hello,\n\n"
            f"Following our analysis of your electricity bills, {company['name']} is pleased "
            f"to share a proposal for a {fmt_int(d.sizing.recommended_kwc)} kWc "
            f"self-consumption solar plant on your site.\n\n"
            f"Key points:\n"
            f"  - Zero investment on your side: we finance, install and operate the plant.\n"
            f"  - A solar tariff guaranteed {d.discount_pct}% below the CIE day tariff.\n"
            f"  - Estimated savings of {fmt_money(s1.annual_saving, lang)} per year, "
            f"i.e. {fmt_money(s1.cumulative_15y_saving, lang)} over {d.contract_years} years.\n"
            f"  - {fmt_int(d.financial.co2_tonnes_per_year)} tonnes of CO2 avoided every year.\n\n"
            f"The attached presentation details the current situation, system sizing, the PPA "
            f"model and the financial analysis.\n\n"
            f"We would be glad to discuss it and arrange a technical site visit.\n\n"
            f"Best regards,\n"
            f"{company['contact_name']}\n{company['contact_role_en']}\n"
            f"{company['contact_email']}"
        )
    return {"subject": subject, "body": body}


def _ai_email(data: ProposalData, lang: str) -> Dict[str, str]:
    """Use Claude to write a tailored email. Raises on any failure."""
    import anthropic

    d = data
    language = "French" if lang == "fr" else "English"
    company = branding()["company"]
    prompt = (
        f"Write a concise, persuasive B2B cover email in {language} from "
        f"{company['name']} (a solar PPA developer) to {d.client.name}, a company in "
        f"the '{d.client.industry}' sector in {d.client.country}. The email accompanies "
        f"a solar proposal. Tone: professional, confident, CFO-friendly, not salesy. "
        f"6-10 sentences. Weave in these facts naturally:\n{_facts(data, lang)}\n\n"
        f"Sign as {company['contact_name']}, {company['contact_email']}.\n"
        f"Return STRICT JSON: {{\"subject\": \"...\", \"body\": \"...\"}}"
    )
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    import json
    import re

    text = msg.content[0].text
    m = re.search(r"\{.*\}", text, re.DOTALL)
    data_json = json.loads(m.group(0))
    if not data_json.get("subject") or not data_json.get("body"):
        raise ValueError("incomplete AI email")
    return {"subject": data_json["subject"], "body": data_json["body"]}


def build_cover_email(data: ProposalData, lang: str = "fr",
                      use_ai: bool = True) -> Dict[str, str]:
    """Return {"subject", "body", "mode"} for the client cover email."""
    if use_ai and os.getenv("ANTHROPIC_API_KEY"):
        try:
            out = _ai_email(data, lang)
            out["mode"] = "ai"
            return out
        except Exception:
            pass  # fall back to the deterministic template
    out = _template(data, lang)
    out["mode"] = "template"
    return out
