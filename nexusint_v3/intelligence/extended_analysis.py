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

    "porters_five_forces": Module(
        id="porters_five_forces",
        icon="⚡",
        label="Porter's Five Forces",
        group="Strategy",
        color="#c084fc",
        description="Structural industry attractiveness: rivalry, buyers, suppliers, substitutes, new entrants",
        system_prompt=(
            "You are a strategic management consultant applying Porter's Five Forces framework. "
            "You give QUANTIFIED assessments — not just high/medium/low but WHY with specific evidence. "
            "Search for real industry data: concentration ratios, switching costs, "
            "capital requirements, substitute availability, supplier power indicators. "
            "Rate each force 1-10 (10 = most threatening to profitability). "
            "Conclude with an overall industry attractiveness score and strategic implications."
        ),
        prompt_builder=lambda b, c, m: (
            f"Porter's Five Forces analysis for the industry that \"{b}\" operates in"
            f"{f' in {c}' if c else ''}.\n\n"
            f"For each force, provide: rating 1-10, specific evidence, key players/factors:\n\n"
            f"1. **Competitive Rivalry** — number of competitors, market concentration, "
            f"price competition intensity, differentiation level, exit barriers\n\n"
            f"2. **Threat of New Entrants** — capital requirements, regulatory barriers, "
            f"brand loyalty, economies of scale, access to distribution\n\n"
            f"3. **Bargaining Power of Buyers** — customer concentration, switching costs, "
            f"price sensitivity, ability to backward integrate\n\n"
            f"4. **Bargaining Power of Suppliers** — supplier concentration, switching costs, "
            f"threat of forward integration, input criticality\n\n"
            f"5. **Threat of Substitutes** — availability of alternatives, switching costs, "
            f"relative price-performance of substitutes\n\n"
            f"Conclude: Overall industry attractiveness score (1-10), "
            f"which forces most threaten \"{b}\" specifically, "
            f"and 3 strategic moves to strengthen their competitive position.\n\n"
            f"{m}"
        ),
    ),

    "business_model_canvas": Module(
        id="business_model_canvas",
        icon="🗃️",
        label="Business Model Canvas",
        group="Strategy",
        color="#67e8f9",
        description="Full 9-block BMC deconstruction with quality scoring per block",
        system_prompt=(
            "You are a business model strategist. "
            "Deconstruct any business using the Business Model Canvas (Osterwalder). "
            "For each block, describe the current state AND rate its quality 1-10. "
            "Identify which blocks are weakest and why. "
            "Search for real information about the business's actual model."
        ),
        prompt_builder=lambda b, c, m: (
            f"Full Business Model Canvas for \"{b}\""
            f"{f' operating in {c}' if c else ''}.\n\n"
            f"For each block: describe current state, quality score 1-10, "
            f"key risks, improvement opportunities.\n\n"
            f"1. **Customer Segments** — who they serve, segmentation logic, TAM/SAM\n"
            f"2. **Value Propositions** — what problem solved, unique value, evidence it works\n"
            f"3. **Channels** — how they reach customers, owned vs rented channels, cost efficiency\n"
            f"4. **Customer Relationships** — type of relationship, retention mechanisms, NPS indicators\n"
            f"5. **Revenue Streams** — all revenue sources, pricing models, revenue quality\n"
            f"6. **Key Resources** — critical assets (physical, IP, human, financial)\n"
            f"7. **Key Activities** — what they must do well to deliver value\n"
            f"8. **Key Partnerships** — critical partners, dependency risks\n"
            f"9. **Cost Structure** — fixed vs variable, major cost drivers, unit economics\n\n"
            f"Conclude: Overall business model quality score, "
            f"the single weakest block that most limits growth, "
            f"and 3 specific model improvements.\n\n"
            f"{m}"
        ),
    ),

    "esg": Module(
        id="esg",
        icon="🌱",
        label="ESG Assessment",
        group="Intelligence",
        color="#4ade80",
        description="Environmental, Social, Governance risk and opportunity scoring",
        system_prompt=(
            "You are an ESG analyst. Search for real ESG data: "
            "emissions disclosures, sustainability reports, governance structures, "
            "labour practices, board composition, controversies, ESG ratings from "
            "MSCI, Sustainalytics, or local equivalents. "
            "Score each pillar 1-10 (10 = best practice). "
            "Identify material ESG risks that could impact valuation or operations. "
            "Consider regional ESG expectations — different markets have different baselines."
        ),
        prompt_builder=lambda b, c, m: (
            f"ESG assessment for \"{b}\"{f' in {c}' if c else ''}.\n\n"
            f"Search for sustainability reports, ESG ratings, controversies, governance data.\n\n"
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
        icon="⚖️",
        label="Regulatory Risk",
        group="Intelligence",
        color="#fca5a5",
        description="Compliance exposure, pending regulations, enforcement history",
        system_prompt=(
            "You are a regulatory risk specialist. "
            "Search for: current regulatory framework, pending legislation, "
            "enforcement actions in this sector, compliance requirements, "
            "regulatory changes in the last 12 months, upcoming changes expected. "
            "Map each regulatory risk to financial impact potential. "
            "Be specific about which regulations apply and what non-compliance costs."
        ),
        prompt_builder=lambda b, c, m: (
            f"Regulatory risk mapping for \"{b}\"{f' in {c}' if c else ''}.\n\n"
            f"Search for current and pending regulations affecting their sector.\n\n"
            f"**Current Regulatory Exposure:**\n"
            f"- List all material regulations they must comply with\n"
            f"- Compliance cost estimate where knowable\n"
            f"- Known violations or enforcement actions (search news and regulatory filings)\n\n"
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

    "management_quality": Module(
        id="management_quality",
        icon="👥",
        label="Management Quality",
        group="Intelligence",
        color="#fdba74",
        description="Leadership assessment: track record, capability gaps, succession risk",
        system_prompt=(
            "You are a management due diligence specialist. "
            "Search for: leadership team backgrounds on LinkedIn, news coverage, "
            "past company track records, board composition, investor communications, "
            "Glassdoor leadership ratings, founder backgrounds. "
            "Assess management quality objectively — strengths and concerning gaps. "
            "This is critical due diligence work, not a flattery exercise."
        ),
        prompt_builder=lambda b, c, m: (
            f"Management quality assessment for \"{b}\"{f' in {c}' if c else ''}.\n\n"
            f"Search for leadership backgrounds, track records, and management signals.\n\n"
            f"**Leadership Team:**\n"
            f"- CEO: background, relevant experience, tenure, public communications quality\n"
            f"- CFO: financial expertise, credibility signals\n"
            f"- Other key executives: relevant capabilities\n\n"
            f"**Track Record Assessment:**\n"
            f"- What has this team built/achieved previously?\n"
            f"- Any failures or controversies in their history?\n"
            f"- Strategic decisions made — quality of capital allocation\n\n"
            f"**Red Flags:**\n"
            f"- High executive turnover\n"
            f"- Insider selling patterns (for listed companies)\n"
            f"- Discrepancy between stated strategy and actions\n"
            f"- Governance concerns\n\n"
            f"**Capability Gaps:**\n"
            f"- Skills missing from leadership team for the company's next stage\n"
            f"- Succession risk (key person dependency)\n\n"
            f"**Management Score: 1-10** with specific reasoning.\n\n"
            f"{m}"
        ),
    ),

    "pestle": Module(
        id="pestle",
        icon="🔭",
        label="PESTLE Analysis",
        group="Core",
        color="#a5b4fc",
        description="Political, Economic, Social, Technological, Legal, Environmental factors",
        system_prompt=(
            "You are a strategic analyst conducting a PESTLE analysis. "
            "Search for real, current data on each factor. "
            "Rate each factor's impact: Positive/Negative/Neutral, "
            "magnitude 1-10, time horizon (now/1yr/3yr+). "
            "Be specific — name actual policies, trends, technologies, laws. "
            "Conclude with the 3 most significant PESTLE factors for this business."
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

    "scenario_planning": Module(
        id="scenario_planning",
        icon="🎲",
        label="Scenario Planning",
        group="Strategy",
        color="#f9a8d4",
        description="3 strategic scenarios with financial implications and response playbooks",
        system_prompt=(
            "You are a scenario planning specialist. "
            "Build 3 distinct, plausible scenarios for this business over 3 years: "
            "Base Case (most likely), Downside (significant headwinds), Upside (tailwinds align). "
            "Each scenario must have: specific triggering conditions, "
            "quantified revenue and EBITDA impact, and a management response playbook. "
            "Scenarios must be internally consistent — each tells a coherent story."
        ),
        prompt_builder=lambda b, c, m: (
            f"Strategic scenario planning for \"{b}\"{f' in {c}' if c else ''}.\n\n"
            f"Build 3 scenarios for the next 36 months:\n\n"
            f"**BASE CASE (60% probability):**\n"
            f"- Key assumptions\n"
            f"- Revenue trajectory\n"
            f"- Critical success factors\n"
            f"- Management priorities\n\n"
            f"**DOWNSIDE SCENARIO (25% probability):**\n"
            f"- Triggering conditions (macro, competitive, operational)\n"
            f"- Revenue/EBITDA impact vs base case\n"
            f"- Warning signals to watch for\n"
            f"- Response playbook: what management should do if this scenario emerges\n\n"
            f"**UPSIDE SCENARIO (15% probability):**\n"
            f"- What needs to go right simultaneously\n"
            f"- Revenue/EBITDA upside vs base case\n"
            f"- How to position to capture this scenario\n"
            f"- Investment required to realise the upside\n\n"
            f"Conclude: The single decision management should make TODAY that keeps "
            f"options open across all 3 scenarios.\n\n"
            f"{m}"
        ),
    ),

    "digital_maturity": Module(
        id="digital_maturity",
        icon="💻",
        label="Digital Maturity",
        group="Operations",
        color="#7dd3fc",
        description="Technology stack assessment, digital gaps, AI readiness, cybersecurity posture",
        system_prompt=(
            "You are a digital transformation and technology strategy expert. "
            "Assess the digital maturity of any business: "
            "their current technology stack, digital channels, data capabilities, "
            "AI/automation adoption, cybersecurity posture, and tech debt. "
            "Search for job postings (reveals tech stack), product reviews, "
            "LinkedIn engineer profiles, and news about tech initiatives. "
            "Score digital maturity 1-10 across five dimensions."
        ),
        prompt_builder=lambda b, c, m: (
            f"Digital maturity assessment for \"{b}\"{f' in {c}' if c else ''}.\n\n"
            f"Search job postings, engineer profiles, product tech signals.\n\n"
            f"**Digital Channels (score 1-10):**\n"
            f"- Website/app quality and functionality\n"
            f"- E-commerce/digital revenue share\n"
            f"- Mobile-first capability\n\n"
            f"**Data and Analytics (score 1-10):**\n"
            f"- Data infrastructure signals\n"
            f"- Analytics capability (BI tools, data team)\n"
            f"- Customer data utilisation\n\n"
            f"**AI and Automation (score 1-10):**\n"
            f"- Current AI/ML applications\n"
            f"- Process automation maturity\n"
            f"- AI readiness (data quality, talent)\n\n"
            f"**Cybersecurity (score 1-10):**\n"
            f"- Known breaches or incidents (search news)\n"
            f"- Security certifications (ISO 27001, SOC2)\n"
            f"- Security team signals\n\n"
            f"**Technology Stack (score 1-10):**\n"
            f"- Core platform age and flexibility\n"
            f"- Cloud adoption\n"
            f"- Tech debt indicators\n\n"
            f"Conclude: Overall digital maturity score, "
            f"top 3 digital investments that would generate highest ROI, "
            f"digital risks that could disrupt the business in 24 months.\n\n"
            f"{m}"
        ),
    ),
}
