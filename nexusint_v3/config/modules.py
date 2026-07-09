"""
config/modules.py — All 11 analysis module definitions.

Each module has: metadata, system prompt, and a prompt builder function.
Prompts instruct the model to reason over pre-computed, locally-collected data
(PSX financials, FinBERT scores, ranked news, compliance rules); they do not rely
on live web-search grounding. Live web data, where used, comes from DuckDuckGo in
the collection stage, not from the model.
"""

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Module:
    id: str
    icon: str
    label: str
    group: str           # Core | Intelligence | Strategy | Operations
    color: str           # hex accent colour
    description: str
    system_prompt: str
    prompt_builder: Callable[[str, str, str], str]  # business, country, memory_context
    requires_web_search: bool = True       # False → no Google Search grounding (pre-computed data sufficient)
    local_data_sufficient: bool = False    # True → skip via Express mode / warn via Gated mode if local analysis fails


MODULES: dict[str, Module] = {

    "overview": Module(
        id="overview",
        icon="",
        label="Overview",
        group="Core",
        color="#e8ff47",
        description="Company profile: business model, revenue, structure, leadership, PSX data, health score",
        system_prompt=(
            "Write a structured company overview using only the pre-computed data provided. "
            "Sections: business description, revenue and scale, organisational structure, "
            "key leadership (from PSX data), financial health score with rationale. "
            "State figures as provided. Do not search for additional information. "
            "Do not add strategy recommendations. Use plain prose, no bullet spam."
        ),
        prompt_builder=lambda b, c, m: (
            f'Business overview of "{b}"{f" operating in {c}" if c else ""}.\n\n'
            f'Use only the PSX profile, computed financials, and health score provided in the context.\n\n'
            f'Deliver:\n'
            f'1. **Business Model** - how they make money, from the provided profile\n'
            f'2. **Scale** - revenue and size from the computed financials\n'
            f'3. **Leadership** - key executives present in the PSX data\n'
            f'4. **Products/Services** - what they sell, per the profile\n'
            f'5. **Recent Developments** - from the provided PSX announcements\n'
            f'6. **SWOT** - grounded in the computed ratios and signals provided\n'
            f'7. **Health Score** - restate the provided score with its rationale\n\n'
            f'{m}'
        ),
    ),

    "competitors": Module(
        id="competitors",
        icon="",
        label="Competitors",
        group="Core",
        color="#ff6b35",
        description="PSX sector peers: relative financial positioning from computed ratios",
        system_prompt=(
            "Write a competitive positioning summary using only the peer comparison data provided. "
            "Describe how the subject company's margins, leverage, and growth compare to "
            "sector peers listed in the data. State which peers lead on which metrics. "
            "Do not name companies not present in the provided peer list. "
            "Do not recommend strategic actions."
        ),
        prompt_builder=lambda b, c, m: (
            f'Competitive positioning for "{b}"{f" in {c}" if c else ""}.\n\n'
            f'Use only the PSX sector peer comparison provided in the context.\n\n'
            f'For each peer in the provided data:\n'
            f'- Relative margin, leverage, and growth versus the subject company\n'
            f'- Which peer leads on which metric\n\n'
            f'Deliver a positioning summary showing where "{b}" sits within its '
            f'sector on the provided metrics.\n\n'
            f'{m}'
        ),
    ),

    "sentiment": Module(
        id="sentiment",
        icon="",
        label="Sentiment",
        group="Intelligence",
        color="#a78bfa",
        description="PSX announcement classification and news feed sentiment scores from FinBERT",
        system_prompt=(
            "Write a sentiment summary using only the FinBERT scores and announcement "
            "classifications provided. State the distribution across positive, negative, "
            "and procedural announcements. Note any high-severity signals. "
            "Do not reference social media, Reddit, Glassdoor, or any platform "
            "not present in the provided data."
        ),
        prompt_builder=lambda b, c, m: (
            f'Sentiment summary for "{b}"{f" in {c}" if c else ""}.\n\n'
            f'Use only the FinBERT scores, PSX announcement classifications, and ranked '
            f'news headlines provided in the context below. Do not search social platforms.\n\n'
            f'Deliver:\n'
            f'1. **Announcement Sentiment** - distribution across positive, negative, and procedural filings\n'
            f'2. **News Sentiment** - what the ranked headlines and FinBERT scores indicate\n'
            f'3. **High-Severity Signals** - any material positive or negative items in the data\n'
            f'4. **Net Read** - overall sentiment balance based strictly on the provided scores\n\n'
            f'{m}'
        ),
    ),

    "financial": Module(
        id="financial",
        icon="",
        label="Financials",
        group="Strategy",
        color="#34d399",
        description="Financial ratios, Altman Z-Score, DuPont decomposition from PSX or uploaded statements",
        system_prompt=(
            "Write financial commentary using only the pre-computed ratios provided. "
            "Interpret each ratio relative to the sector median where provided. "
            "Flag ratios in warning zones. State the data source and period. "
            "Do not re-derive or search for figures. Do not recommend financing actions."
        ),
        prompt_builder=lambda b, c, m: (
            f'Comprehensive financial strategy for "{b}"{f" in {c}" if c else ""}.\n\n'
            f'The pre-computed financial ratios, DCF valuation, and health metrics are '
            f'provided in the context below. Base your analysis on these numbers.\n\n'
            f'Deliver:\n'
            f'1. **Financial Health Commentary** - interpret the provided ratios and health score\n'
            f'2. **Revenue Optimisation** - pricing, new streams, upsells\n'
            f'3. **Cost Analysis** - structure and reduction opportunities based on margin data\n'
            f'4. **Funding Strategy** - what fits their stage (VC/PE/grants/debt)\n'
            f'5. **Cash Flow Management** - specific tactics based on liquidity ratios\n'
            f'6. **Financial KPIs** - what to track vs the benchmarks provided\n'
            f'7. **Tax Optimisation** - jurisdiction-specific strategies\n\n'
            + (f'Include {c}-specific tax laws, incentives, and financial regulations.\n\n' if c else '')
            + m
        ),
        requires_web_search=False,
        local_data_sufficient=True,
    ),

    "country": Module(
        id="country",
        icon="",
        label="Country Intel",
        group="Intelligence",
        color="#38bdf8",
        description="Regulatory framework, macro indicators, and sector-specific compliance requirements",
        system_prompt=(
            "Write a country and regulatory context section using only the macro data "
            "and compliance rules provided. State SBP rate, CPI, GDP, and relevant "
            "regulatory bodies for the sector. Describe key compliance obligations. "
            "Do not add subjective country risk commentary not supported by the data provided."
        ),
        prompt_builder=lambda b, c, m: (
            f'Market intelligence for "{b}" '
            f'{"entering " + c if c else "- identify their primary market"}.\n\n'
            f'Deliver for each relevant market:\n'
            f'1. **Legal Requirements** - registration, licenses, tax, employment law\n'
            f'2. **Ground Truth** - real vs official timelines, corruption index, '
            f'who you actually need to know, informal costs\n'
            f'3. **Banking Reality** - account access ease, FX controls, profit repatriation\n'
            f'4. **Cultural Business Norms** - relationship vs transactional, negotiation style\n'
            f'5. **Political Risk** - stability, currency risk, regulatory predictability\n'
            f'6. **Recent Developments** - what has changed in the last 12 months\n\n'
            f'{m}'
        ),
    ),

    "scaling": Module(
        id="scaling",
        icon="",
        label="Scaling",
        group="Strategy",
        color="#818cf8",
        description="Revenue trend, market share estimate, and sector growth rate from available data",
        system_prompt=(
            "Write a scaling context summary using only the revenue trend, sector size, "
            "and growth data provided. State what the data shows about the company's "
            "trajectory relative to sector. Do not recommend specific scaling actions, "
            "M&A targets, or strategic moves."
        ),
        prompt_builder=lambda b, c, m: (
            f'Scaling playbook for "{b}"{f" - primary market {c}" if c else ""}.\n\n'
            f'Deliver:\n'
            f'1. **Growth Stage** - where they are on the S-curve\n'
            f'2. **#1 Constraint** - the single biggest thing limiting 10x growth\n'
            f'3. **Top 10 Scaling Moves** - ranked by impact/effort, with specific detail\n'
            f'4. **Growth Constraints** - operational limits implied by the ratio and sector data\n'
            f'5. **3 Fatal Mistakes** - what kills companies at this stage in this sector\n'
            f'6. **12-Month Roadmap** - specific milestones and decision gates\n'
            f'7. **The $1B Version** - what does this business look like at full scale\n\n'
            f'{m}'
        ),
    ),

    "valuation": Module(
        id="valuation",
        icon="",
        label="Valuation",
        group="Core",
        color="#fbbf24",
        description="DCF scenarios, sector multiples, and equity value range from computed financials",
        system_prompt=(
            "Write a valuation summary using only the pre-computed DCF and multiple data provided. "
            "State bear, base, and bull equity values with the key assumptions. "
            "Compare multiples to sector peers where data is provided. "
            "Do not name acquirers. Do not recommend exit strategies."
        ),
        prompt_builder=lambda b, c, m: (
            f'Comprehensive valuation of "{b}"{f" - market {c}" if c else ""}.\n\n'
            f'The DCF model, financial ratios, and health score are pre-computed and '
            f'provided in the context. Use these as the foundation of your valuation analysis.\n\n'
            f'Deliver:\n'
            f'1. **Valuation Summary** - interpret the provided DCF bear/base/bull range\n'
            f'2. **Comparable Company Multiples** - cross-reference with sector peers\n'
            f'3. **Key Assumptions** - what drives the valuation and sensitivity factors\n'
            f'4. **Value Drivers and Destroyers** - what moves the number, based on the provided sensitivity data\n'
            f'5. **Valuation Range** - reconcile the DCF scenarios with the peer multiples\n'
            f'6. **Key Sensitivities** - which assumptions the value is most sensitive to\n'
            f'7. **Valuation Read** - where the number lands versus current price, with reasoning\n\n'
            f'{m}'
        ),
        requires_web_search=False,
        local_data_sufficient=True,
    ),

    "economy": Module(
        id="economy",
        icon="",
        label="Economy",
        group="Core",
        color="#00d4ff",
        description="SBP rate, CPI, GDP, forex rates, and sector macro indicators",
        system_prompt=(
            "Write a macro environment summary using only the indicators provided: "
            "policy rate, inflation, GDP growth, forex rates, and any sector-specific "
            "government data. State what each indicator means for this sector specifically. "
            "Do not add global macro commentary not present in the provided data."
        ),
        prompt_builder=lambda b, c, m: (
            f'Macroeconomic environment analysis for "{b}"\'s sector'
            f'{f" in {c}" if c else ""}.\n\n'
            f'Using the macro indicators provided in the context, translate them to business impact.\n\n'
            f'Deliver:\n'
            f'1. **Current Macro Snapshot** - GDP, inflation, rates, employment\n'
            f'2. **Sector-Specific Conditions** - how macro hits their industry\n'
            f'3. **Tailwinds** - macro forces working in their favour\n'
            f'4. **Headwinds** - macro forces working against them\n'
            f'5. **Timing Windows** - near-term opportunities or risks (next 12 months)\n'
            f'6. **Scenario Analysis** - base case vs downside macro scenario impact\n\n'
            f'{m}'
        ),
    ),

    "worldnews": Module(
        id="worldnews",
        icon="",
        label="World Intel (News Feed)",
        group="Core",
        color="#f59e0b",
        description="Relevance-filtered and FinBERT-scored news headlines for this company and sector",
        system_prompt=(
            "Write a news intelligence summary using only the headlines provided, "
            "which have already been filtered for relevance and scored for sentiment. "
            "Group by theme. State tailwind and headwind counts. "
            "Do not reference headlines not in the provided list."
        ),
        prompt_builder=lambda b, c, m: (
            f'Global intelligence brief for "{b}"{f" operating in {c}" if c else ""}.\n\n'
            f'Using only the relevance-filtered, FinBERT-scored headlines provided in the context, '
            f'summarise the developments affecting their sector.\n\n'
            f'For each significant development:\n'
            f'- Type: OPPORTUNITY or THREAT\n'
            f'- Magnitude: 1-10\n'
            f'- Time horizon: Immediate / 6 months / 1 year+\n'
            f'- Specific business impact for "{b}"\n\n'
            f'Deliver 8-12 items. Prioritise by magnitude.\n\n'
            f'{m}'
        ),
    ),
}


MODULE_GROUPS = {
    "Core":         {"color": "#e8ff47", "icon": "◈"},
    "Intelligence": {"color": "#a78bfa", "icon": "◉"},
    "Strategy":     {"color": "#f472b6", "icon": "◆"},
    "Operations":   {"color": "#fb923c", "icon": "◇"},
}


def get_module(module_id: str) -> Optional[Module]:
    return MODULES.get(module_id)


def get_modules_by_group() -> dict[str, list[Module]]:
    groups: dict[str, list[Module]] = {}
    for mod in MODULES.values():
        if mod.group not in groups:
            groups[mod.group] = []
        groups[mod.group].append(mod)
    return groups
