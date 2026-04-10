"""
CloudCarbon Executive Report Generator.
Uses Anthropic Claude to produce narrative summaries from aggregated metrics.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()


@dataclass
class ReportData:
    period_start: str
    period_end: str
    total_cost_usd: float
    total_co2e_kg: float
    scope3_co2e_kg: float
    scope3_pct: float
    total_water_litres: float
    water_stress_adjusted_litres: float
    carbon_efficiency: float
    cost_mom_pct: float
    co2e_mom_pct: float
    water_mom_pct: float
    top_provider_by_cost: str
    top_provider_by_carbon: str
    top_service_by_carbon: str
    open_recommendations_count: int
    total_cost_opportunity_usd: float
    total_co2e_opportunity_kg: float
    high_stress_water_regions: list[str]


def generate_executive_summary(data: ReportData) -> dict:
    prompt = f"""You are writing the executive summary section of a cloud sustainability report for a technology organization.

Report period: {data.period_start} to {data.period_end}

Key metrics:
- Total cloud spend: ${data.total_cost_usd:,.0f}
- Total carbon emissions: {data.total_co2e_kg:,.0f} kg CO2e
- Scope 3 emissions: {data.scope3_co2e_kg:,.0f} kg CO2e ({data.scope3_pct:.1f}% of total)
- Total water consumption: {data.total_water_litres:,.0f} litres
- Water stress adjusted: {data.water_stress_adjusted_litres:,.0f} litres
- Carbon efficiency: {data.carbon_efficiency:.2f} kg CO2e per $1,000 spend
- Month-over-month cost change: {data.cost_mom_pct:+.1f}%
- Month-over-month carbon change: {data.co2e_mom_pct:+.1f}%
- Month-over-month water change: {data.water_mom_pct:+.1f}%
- Top provider by cost: {data.top_provider_by_cost}
- Top provider by carbon: {data.top_provider_by_carbon}
- Highest carbon service: {data.top_service_by_carbon}
- Open optimization recommendations: {data.open_recommendations_count}
- Potential monthly cost savings: ${data.total_cost_opportunity_usd:,.0f}
- Potential monthly carbon reduction: {data.total_co2e_opportunity_kg:,.0f} kg CO2e
- High water stress regions: {', '.join(data.high_stress_water_regions) if data.high_stress_water_regions else 'None identified'}

Write the following three sections. Be direct, factual, and specific. Do not use filler phrases like "it is important to note" or "in conclusion". Use the actual numbers from the data provided.

Return your response as a JSON object with exactly these three keys:
{{
  "summary": "A 3-sentence executive summary covering overall performance, carbon footprint trend, and water risk.",
  "highlights": ["highlight 1", "highlight 2", "highlight 3"],
  "recommendations_narrative": "A 2-sentence narrative about the optimization opportunity, citing specific dollar and carbon figures."
}}

Return only the JSON object, no other text."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Executive summary generation failed, using fallback: %s", exc)
        return {
            "summary": (
                f"Cloud spend totalled ${data.total_cost_usd:,.0f} for the period with "
                f"{data.total_co2e_kg:,.0f} kg CO2e in total emissions. "
                f"Carbon changed {data.co2e_mom_pct:+.1f}% month-over-month. "
                f"Water consumption reached {data.total_water_litres:,.0f} litres with "
                f"{len(data.high_stress_water_regions)} high-stress regions identified."
            ),
            "highlights": [
                f"Scope 3 emissions represent {data.scope3_pct:.1f}% of total carbon footprint",
                f"{data.open_recommendations_count} optimization opportunities identified",
                f"${data.total_cost_opportunity_usd:,.0f}/month in potential savings available",
            ],
            "recommendations_narrative": (
                f"Implementing the {data.open_recommendations_count} open recommendations could "
                f"reduce monthly spend by ${data.total_cost_opportunity_usd:,.0f} and carbon "
                f"emissions by {data.total_co2e_opportunity_kg:,.0f} kg CO2e."
            ),
        }
