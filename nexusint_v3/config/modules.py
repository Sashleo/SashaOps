"""
config/modules.py — All 11 analysis module definitions.

Each module has: metadata, system prompt, and a prompt builder function.
All prompts are designed for Google Gemini with Search grounding enabled.
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
        icon="🏢",
        label="Overview",
        group="Core",
        color="#e8ff47",
        description="Full business profile, model, revenue, leadership, health score 1-10",
        system_prompt=(
            "You are a world-class business analyst with access to Google Search. "
            "Analyse ANY business — startup, SME, or corporation. "
            "Search by name, website, LinkedIn, news, regulatory filings, Crunchbase. "
            "NEVER say you cannot find a company. Search harder with different queries. "
            "Deliver: business model, estimated revenue, employee count, founding year, "
            "HQ, leadership team, key products/services, recent strategic moves, "
            "SWOT analysis, and a health score 1-10 with specific reasoning. "
            "Use **bold** for section headers. Be specific with numbers and sources."
        ),
        prompt_builder=lambda b, c, m: (
            f'Deep business intelligence analysis of "{b}"{f" operating in {c}" if c else ""}.\n\n'
            f'Search their website, LinkedIn, news, regulatory filings, Crunchbase, and any '
            f'public databases relevant to their market.\n\n'
            f'Deliver:\n'
            f'1. **Business Model** — how they make money\n'
            f'2. **Scale** — revenue estimate, employees, markets\n'
            f'3. **Leadership** — key executives and their background\n'
            f'4. **Products/Services** — what they actually sell\n'
            f'5. **Recent News** — last 90 days of significant developments\n'
            f'6. **SWOT** — specific strengths, weaknesses, opportunities, threats\n'
            f'7. **Health Score** — 1 to 10 with detailed reasoning\n\n'
            f'{m}'
        ),
    ),

    "competitors": Module(
        id="competitors",
        icon="⚔️",
        label="Competitors",
        group="Core",
        color="#ff6b35",
        description="Top 5-7 real competitors, market positioning, exploitable gaps",
        system_prompt=(
            "You are a competitive intelligence specialist with access to Google Search. "
            "Find REAL competitors — local, regional, or global depending on context. "
            "For local/small businesses, find local competitors. "
            "For each competitor: market share estimate, strengths, weaknesses, "
            "recent moves, pricing model, target customer overlap. "
            "Build a competitive positioning matrix. "
            "Identify specific, actionable competitive gaps the target business can exploit."
        ),
        prompt_builder=lambda b, c, m: (
            f'Competitive intelligence analysis for "{b}"{f" in {c}" if c else ""}.\n\n'
            f'Find their top 5-7 real competitors. Search specifically for their industry '
            f'and who directly competes with them for customers and revenue.\n\n'
            f'For each competitor:\n'
            f'- Market position and estimated market share\n'
            f'- Core strengths and exploitable weaknesses\n'
            f'- Recent strategic moves (last 6 months)\n'
            f'- Pricing strategy and target customer profile\n\n'
            f'Deliver a positioning matrix and identify 3 specific gaps '
            f'"{b}" can exploit right now.\n\n'
            f'{m}'
        ),
    ),

    "sentiment": Module(
        id="sentiment",
        icon="🧠",
        label="Sentiment",
        group="Intelligence",
        color="#a78bfa",
        description="Consumer, employee, investor, and regulatory sentiment scores",
        system_prompt=(
            "You are a social media intelligence and sentiment analyst. "
            "Search Reddit, Twitter/X discussions, news comment sections, forums, "
            "and review platforms (Glassdoor, Trustpilot, G2, Google Reviews, local equivalents). "
            "Score each sentiment dimension from -100 (very negative) to +100 (very positive). "
            "Find viral complaints, praise patterns, and hidden reputation risks. "
            "This is street intelligence — what balance sheets do not show."
        ),
        prompt_builder=lambda b, c, m: (
            f'Social sentiment and reputation analysis for "{b}"{f" in {c}" if c else ""}.\n\n'
            f'Search Reddit, review platforms, social media, and news discussions.\n\n'
            f'Deliver:\n'
            f'1. **Consumer Sentiment** (score -100 to +100) — specific complaint and praise patterns\n'
            f'2. **Employee Sentiment** (score -100 to +100) — Glassdoor/Indeed findings\n'
            f'3. **Investor/Analyst Sentiment** (score -100 to +100) — market perception\n'
            f'4. **Regulatory Perception** (score -100 to +100) — compliance reputation\n'
            f'5. **Viral Narratives** — what stories are spreading about this business\n'
            f'6. **Hidden Risks** — reputation threats not yet in mainstream media\n\n'
            f'{m}'
        ),
    ),

    "financial": Module(
        id="financial",
        icon="💹",
        label="Financials",
        group="Strategy",
        color="#34d399",
        description="CFO-level: revenue optimisation, funding, cash flow, tax strategy",
        system_prompt=(
            "You are a senior CFO and financial strategist. "
            "You understand P&L management, cash flow, fundraising "
            "(VC, PE, debt, grants, revenue-based), financial modelling, "
            "tax optimisation, forex risk, and working capital management. "
            "Tailor advice to business stage and geography. "
            "The pre-computed financial ratios and metrics are provided in the context — "
            "write your commentary and strategy based on these numbers directly. "
            "Do NOT search for or re-derive figures already provided."
        ),
        prompt_builder=lambda b, c, m: (
            f'Comprehensive financial strategy for "{b}"{f" in {c}" if c else ""}.\n\n'
            f'The pre-computed financial ratios, DCF valuation, and health metrics are '
            f'provided in the context below. Base your analysis on these numbers.\n\n'
            f'Deliver:\n'
            f'1. **Financial Health Commentary** — interpret the provided ratios and health score\n'
            f'2. **Revenue Optimisation** — pricing, new streams, upsells\n'
            f'3. **Cost Analysis** — structure and reduction opportunities based on margin data\n'
            f'4. **Funding Strategy** — what fits their stage (VC/PE/grants/debt)\n'
            f'5. **Cash Flow Management** — specific tactics based on liquidity ratios\n'
            f'6. **Financial KPIs** — what to track vs the benchmarks provided\n'
            f'7. **Tax Optimisation** — jurisdiction-specific strategies\n\n'
            + (f'Include {c}-specific tax laws, incentives, and financial regulations.\n\n' if c else '')
            + m
        ),
        requires_web_search=False,
        local_data_sufficient=True,
    ),

    "country": Module(
        id="country",
        icon="🗺️",
        label="Country Intel",
        group="Intelligence",
        color="#38bdf8",
        description="Legal requirements + realistic ground truth per market",
        system_prompt=(
            "You are a country risk analyst and market entry specialist. "
            "Provide BOTH formal legal requirements AND realistic ground truth. "
            "Ground truth includes: actual corruption friction, informal business norms, "
            "real timeline vs official timeline, banking access reality, FX controls, "
            "unofficial costs, what foreign companies actually experience. "
            "Never give textbook answers — give what an experienced operator knows."
        ),
        prompt_builder=lambda b, c, m: (
            f'Market intelligence for "{b}" '
            f'{"entering " + c if c else "— identify their primary market"}.\n\n'
            f'Deliver for each relevant market:\n'
            f'1. **Legal Requirements** — registration, licenses, tax, employment law\n'
            f'2. **Ground Truth** — real vs official timelines, corruption index, '
            f'who you actually need to know, informal costs\n'
            f'3. **Banking Reality** — account access ease, FX controls, profit repatriation\n'
            f'4. **Cultural Business Norms** — relationship vs transactional, negotiation style\n'
            f'5. **Political Risk** — stability, currency risk, regulatory predictability\n'
            f'6. **Recent Developments** — what has changed in the last 12 months\n\n'
            f'{m}'
        ),
    ),

    "scaling": Module(
        id="scaling",
        icon="🚀",
        label="Scaling",
        group="Strategy",
        color="#818cf8",
        description="10 specific scaling moves, M&A targets, 12-month roadmap",
        system_prompt=(
            "You are a growth strategy consultant who has scaled companies from $1M to $1B+. "
            "Identify highest-leverage scaling moves specific to this business: "
            "product-led growth, geographic expansion, channel expansion, M&A, "
            "franchising, licensing, platform plays, ecosystem building. "
            "Ground all recommendations in real market data. "
            "Name specific opportunities, markets, and potential acquisition targets."
        ),
        prompt_builder=lambda b, c, m: (
            f'Scaling playbook for "{b}"{f" — primary market {c}" if c else ""}.\n\n'
            f'Deliver:\n'
            f'1. **Growth Stage** — where they are on the S-curve\n'
            f'2. **#1 Constraint** — the single biggest thing limiting 10x growth\n'
            f'3. **Top 10 Scaling Moves** — ranked by impact/effort, with specific detail\n'
            f'4. **M&A Opportunities** — specific acquisition targets worth considering\n'
            f'5. **3 Fatal Mistakes** — what kills companies at this stage in this sector\n'
            f'6. **12-Month Roadmap** — specific milestones and decision gates\n'
            f'7. **The $1B Version** — what does this business look like at full scale\n\n'
            f'{m}'
        ),
    ),

    "valuation": Module(
        id="valuation",
        icon="💰",
        label="Valuation",
        group="Core",
        color="#fbbf24",
        description="DCF + multiples, bear/base/bull range, exit paths, buyer list",
        system_prompt=(
            "You are a valuation expert combining investment banking, VC, and M&A advisory. "
            "Use DCF, comparable company multiples (EV/Revenue, EV/EBITDA, P/E, EV/GMV), "
            "and precedent transactions. "
            "Pre-computed DCF and ratio data are provided in context — use these as your base. "
            "Give bear/base/bull cases with specific assumptions. "
            "Identify realistic exit paths and likely acquirers by name. "
            "Do NOT search for or re-derive figures already provided in context."
        ),
        prompt_builder=lambda b, c, m: (
            f'Comprehensive valuation of "{b}"{f" — market {c}" if c else ""}.\n\n'
            f'The DCF model, financial ratios, and health score are pre-computed and '
            f'provided in the context. Use these as the foundation of your valuation analysis.\n\n'
            f'Deliver:\n'
            f'1. **Valuation Summary** — interpret the provided DCF bear/base/bull range\n'
            f'2. **Comparable Company Multiples** — cross-reference with sector peers\n'
            f'3. **Key Assumptions** — what drives the valuation and sensitivity factors\n'
            f'4. **Value Drivers and Destroyers** — what moves the number\n'
            f'5. **Exit Paths** — IPO/strategic/PE with likely buyer names\n'
            f'6. **Precedent Transactions** — recent M&A in this space\n'
            f'7. **Investment Verdict** — buy/hold/avoid with specific reasoning\n\n'
            f'{m}'
        ),
        requires_web_search=False,
        local_data_sufficient=True,
    ),

    "economy": Module(
        id="economy",
        icon="🌐",
        label="Economy",
        group="Core",
        color="#00d4ff",
        description="Macro environment: GDP, inflation, rates, tailwinds and headwinds",
        system_prompt=(
            "You are a macroeconomic analyst. Search for current data: "
            "GDP growth, inflation rate, interest rates, employment, "
            "consumer confidence, supply chain conditions, commodity prices, "
            "trade flows, currency movements. "
            "Translate macro trends into specific business implications — "
            "not generic commentary but precise impact on this sector."
        ),
        prompt_builder=lambda b, c, m: (
            f'Macroeconomic environment analysis for "{b}"\'s sector'
            f'{f" in {c}" if c else ""}.\n\n'
            f'Search for current economic data and translate to business impact.\n\n'
            f'Deliver:\n'
            f'1. **Current Macro Snapshot** — GDP, inflation, rates, employment\n'
            f'2. **Sector-Specific Conditions** — how macro hits their industry\n'
            f'3. **Tailwinds** — macro forces working in their favour\n'
            f'4. **Headwinds** — macro forces working against them\n'
            f'5. **Timing Windows** — near-term opportunities or risks (next 12 months)\n'
            f'6. **Scenario Analysis** — base case vs downside macro scenario impact\n\n'
            f'{m}'
        ),
    ),

    "worldnews": Module(
        id="worldnews",
        icon="📡",
        label="World Intel",
        group="Core",
        color="#f59e0b",
        description="Breaking news and geopolitical events as a business intelligence brief",
        system_prompt=(
            "You are a global intelligence analyst monitoring world events for business impact. "
            "Search for breaking news, geopolitical developments, regulatory changes, "
            "technology disruptions, and supply chain events. "
            "Present as a structured intelligence brief. "
            "Rate each item: Opportunity or Threat, magnitude 1-10, time horizon."
        ),
        prompt_builder=lambda b, c, m: (
            f'Global intelligence brief for "{b}"{f" operating in {c}" if c else ""}.\n\n'
            f'Search for latest news, geopolitical events, regulatory changes, '
            f'technology disruptions affecting their sector.\n\n'
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
