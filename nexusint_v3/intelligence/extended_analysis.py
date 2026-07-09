"""
intelligence/extended_analysis.py — Additional analysis modules beyond the core 11.

A proper senior business analyst also covers:
- Porter's Five Forces
- Business Model Canvas deconstruction
- ESG and sustainability risk assessment
- Regulatory and compliance risk mapping
- Management quality assessment
- Technology and digital maturity
- Scenario planning (3 strategic scenarios)
- PESTLE analysis

These are added as modules 12-19 in the MODULES config.
Each feeds into its own report section and is available as a standalone analysis.
"""

from config.modules import Module

EXTENDED_MODULES: dict[str, Module] = {

    "esg": Module(
        id="esg",
        icon="",
        label="ESG Assessment",
        group="Intelligence",
        color="#4ade80",
        description="Sector-based ESG risk framework and available disclosure signals from PSX filings",
        system_prompt=(
            "Write an ESG section using only the sector compliance framework and any "
            "signals present in the provided PSX data. State which ESG dimensions are "
            "material for this sector. Note where data is absent. "
            "Do not assign ESG scores without supporting data. "
            "Do not reference MSCI, Sustainalytics, or third-party ratings not in the data."
        ),
        prompt_builder=lambda b, c, m: (
            f"ESG assessment for \"{b}\"{f' in {c}' if c else ''}.\n\n"
            f"Using the sector ESG framework and any signals present in the provided data, assess the following.\n\n"
            f"**Environmental (score 1-10):**\n"
            f"- Carbon footprint / emissions disclosure\n"
            f"- Energy source and efficiency\n"
            f"- Water and waste management\n"
            f"- Climate risk exposure (physical and transition)\n\n"
            f"**Social (score 1-10):**\n"
            f"- Labour practices and employee treatment\n"
            f"- Community impact and social licence\n"
            f"- Supply chain labour standards\n"
            f"- Diversity and inclusion data\n"
            f"- Customer data privacy practices\n\n"
            f"**Governance (score 1-10):**\n"
            f"- Board composition and independence\n"
            f"- Executive compensation alignment\n"
            f"- Audit quality and transparency\n"
            f"- Anti-corruption controls\n"
            f"- Shareholder rights\n\n"
            f"Conclude: Overall ESG score, 3 material ESG risks, "
            f"ESG opportunities (green finance, talent attraction, regulatory benefit), "
            f"comparison to regional/industry peers.\n\n"
            f"{m}"
        ),
    ),

    "regulatory_risk": Module(
        id="regulatory_risk",
        icon="",
        label="Regulatory Risk",
        group="Intelligence",
        color="#fca5a5",
        description="Applicable laws, regulator, compliance checklist, and red flag signals",
        system_prompt=(
            "Write a regulatory risk section using the compliance profile provided. "
            "State the primary regulator, governing laws, and key compliance requirements. "
            "Flag any red signals found in announcements or uploaded documents. "
            "Do not add regulatory commentary beyond what the compliance data states."
        ),
        prompt_builder=lambda b, c, m: (
            f"Regulatory risk mapping for \"{b}\"{f' in {c}' if c else ''}.\n\n"
            f"Using the compliance profile provided in the context, map the regulatory exposure.\n\n"
            f"**Current Regulatory Exposure:**\n"
            f"- List all material regulations they must comply with\n"
            f"- Compliance cost estimate where knowable\n"
            f"- Known violations or enforcement actions present in the provided data\n\n"
            f"**Pending Regulatory Changes:**\n"
            f"- Regulations proposed or under consultation in next 12-24 months\n"
            f"- Probability of passage and timeline\n"
            f"- Financial impact if enacted (cost increase, revenue impact, market access)\n\n"
            f"**Regulatory Risk Matrix:**\n"
            f"For each risk: Likelihood (1-5) × Impact (1-5) = Risk Score\n\n"
            f"**Regulatory Opportunities:**\n"
            f"- Licences or certifications that open new markets\n"
            f"- Government programmes or incentives available\n"
            f"- First-mover advantage in upcoming regulatory frameworks\n\n"
            f"{m}"
        ),
    ),

    "pestle": Module(
        id="pestle",
        icon="",
        label="PESTLE Analysis",
        group="Core",
        color="#a5b4fc",
        description="PESTLE factors scored from macro data, sector rules, and available signals",
        system_prompt=(
            "Write a PESTLE analysis using only the macro indicators, sector compliance "
            "data, and news signals provided. Score each factor with specific reference "
            "to the data provided. Do not rate factors without supporting data in context. "
            "Do not add factors based on general knowledge not grounded in the provided data."
        ),
        prompt_builder=lambda b, c, m: (
            f"PESTLE analysis for \"{b}\"{f' in {c}' if c else ''}.\n\n"
            f"For each factor: current state, impact (Positive/Negative/Neutral), "
            f"magnitude 1-10, time horizon, specific business implication.\n\n"
            f"**Political:** Government stability, trade policies, political risk, "
            f"foreign investment rules, government as customer/competitor\n\n"
            f"**Economic:** GDP growth, inflation, interest rates, currency stability, "
            f"consumer purchasing power, credit availability, commodity exposure\n\n"
            f"**Social:** Demographics, urbanisation, education levels, "
            f"cultural attitudes toward the product/service, income distribution\n\n"
            f"**Technological:** Digitisation trends, AI/automation impact on the sector, "
            f"infrastructure quality (internet, logistics, energy), "
            f"tech adoption rate in target market\n\n"
            f"**Legal:** IP protection quality, contract enforceability, "
            f"labour laws, sector-specific regulations, litigation risk\n\n"
            f"**Environmental:** Climate risk exposure, environmental regulations, "
            f"resource constraints, sustainability requirements from buyers\n\n"
            f"Conclude: Top 3 PESTLE factors management must act on in next 12 months.\n\n"
            f"{m}"
        ),
    ),

}
