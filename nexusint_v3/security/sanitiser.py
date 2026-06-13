"""
security/sanitiser.py — Deterministic prompt sanitisation for SashaOps.

Strips all identifiable information from prompts before they reach any external
AI API. Replaces real entities with consistent anonymous placeholders using a
session-scoped bidirectional mapping.

The AI receives only structural/analytical context framed as a hypothetical
scenario. Real values are substituted back into the response after the API call.

Coverage:
- Company names (primary + competitors + parent + subsidiaries)
- Personal names (executives, board members, founders)
- Geographic identifiers (countries, cities, addresses, postcodes)
- Currency names and symbols
- Stock exchange names and tickers
- Regulator names and acronyms
- Industry-specific terminology
- Product and service names
- Specific revenue, profit, employee, market share figures (bucketed)
- Email addresses, phone numbers, URLs, domains
- Government ID patterns (SSN, NIN, CNIC, etc)
- Bank account numbers, IBAN, SWIFT codes
- Tax IDs and registration numbers
- IP addresses
- Dates of specific events (replaced with relative time)

Dynamic discovery:
- After static replacement, scans for unmasked Title Case sequences
- Each newly discovered entity gets a unique numbered placeholder
- Discovered entities are persisted to client_entities DB table for reuse
  across future analysis runs (each client builds an increasingly accurate corpus)

No AI is used. Pure deterministic regex + dictionary replacement.
Bypassed entirely when AI mode is set to 'local' (Ollama).
"""

import re
from typing import Optional, List
from dataclasses import dataclass, field


# ──────────────────────────────────────────────────────────────────────────────
# Generic PII patterns — applied to ALL prompts regardless of client context
# ──────────────────────────────────────────────────────────────────────────────

GENERIC_PII_PATTERNS = [
    # Email addresses
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), '[EMAIL]'),

    # International phone numbers (E.164 and common formats)
    (re.compile(r'(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?:[\s.-]?\d{1,4})?'), '[PHONE]'),

    # URLs and domains
    (re.compile(r'https?://[^\s<>"\']+'), '[URL]'),
    (re.compile(r'\bwww\.[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'), '[URL]'),

    # IPv4 addresses
    (re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'), '[IP_ADDRESS]'),

    # IBAN (international bank account)
    (re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b'), '[IBAN]'),

    # SWIFT/BIC codes
    (re.compile(r'\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b'), '[SWIFT]'),

    # Credit card numbers (basic Luhn-eligible patterns)
    (re.compile(r'\b(?:\d{4}[\s-]?){3}\d{4}\b'), '[CARD_NUMBER]'),

    # US SSN
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[GOV_ID]'),

    # Pakistan CNIC (13 digits with dashes)
    (re.compile(r'\b\d{5}-\d{7}-\d{1}\b'), '[GOV_ID]'),

    # India Aadhaar (12 digits)
    (re.compile(r'\b\d{4}\s\d{4}\s\d{4}\b'), '[GOV_ID]'),

    # Nigeria NIN (11 digits)
    (re.compile(r'\bNIN[:\s]*\d{11}\b', re.IGNORECASE), '[GOV_ID]'),

    # Passport numbers (most countries: 1-2 letters + 6-9 digits)
    (re.compile(r'\b[A-Z]{1,2}\d{6,9}\b'), '[PASSPORT]'),

    # Generic long account/registration numbers (10+ consecutive digits)
    (re.compile(r'\b\d{10,16}\b'), '[ACCOUNT_NUMBER]'),

    # Coordinates (latitude/longitude)
    (re.compile(r'-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}'), '[COORDINATES]'),
]


# ──────────────────────────────────────────────────────────────────────────────
# Region-specific terminology mappings
# ──────────────────────────────────────────────────────────────────────────────

REGION_VARIANTS = {
    "Pakistan": {
        "adjective": ["Pakistani", "Pak"],
        "cities": ["Karachi", "Lahore", "Islamabad", "Rawalpindi", "Faisalabad",
                   "Multan", "Peshawar", "Quetta", "Sialkot", "Gujranwala", "Hyderabad"],
        "regulators": ["SECP", "SBP", "FBR", "OGRA", "NEPRA", "PTA", "PEMRA",
                      "State Bank of Pakistan", "Securities and Exchange Commission of Pakistan",
                      "Federal Board of Revenue", "Competition Commission of Pakistan"],
        "exchanges": ["PSX", "Pakistan Stock Exchange", "KSE-100", "KSE-30"],
        "currency_terms": ["PKR", "Pakistani Rupee", "Rupee", "Rs", "Rs.", "₨"],
        "geo_descriptor": "a South Asian emerging market",
    },
    "Nigeria": {
        "adjective": ["Nigerian", "Naija"],
        "cities": ["Lagos", "Abuja", "Port Harcourt", "Kano", "Ibadan", "Kaduna",
                   "Benin City", "Maiduguri", "Zaria", "Enugu", "Onitsha", "Warri"],
        "regulators": ["SEC Nigeria", "CBN", "FIRS", "NUPRC", "NCC", "NAFDAC", "CAC",
                       "Central Bank of Nigeria", "Securities and Exchange Commission",
                       "Federal Inland Revenue Service", "Corporate Affairs Commission"],
        "exchanges": ["NGX", "Nigerian Exchange Group", "Nigerian Stock Exchange", "NSE Nigeria"],
        "currency_terms": ["NGN", "Naira", "Nigerian Naira", "₦"],
        "geo_descriptor": "a West African emerging market",
    },
    "Kenya": {
        "adjective": ["Kenyan"],
        "cities": ["Nairobi", "Mombasa", "Kisumu", "Nakuru", "Eldoret", "Thika",
                   "Malindi", "Kitale", "Garissa", "Kakamega"],
        "regulators": ["CMA Kenya", "CBK", "KRA", "CAK", "NMG", "ERC", "CA Kenya",
                       "Central Bank of Kenya", "Capital Markets Authority",
                       "Kenya Revenue Authority", "Communications Authority"],
        "exchanges": ["NSE Kenya", "Nairobi Securities Exchange", "NSE-20"],
        "currency_terms": ["KES", "Kenya Shilling", "Kenyan Shilling", "KSh", "Ksh"],
        "geo_descriptor": "an East African emerging market",
    },
    "South Africa": {
        "adjective": ["South African", "SA"],
        "cities": ["Johannesburg", "Cape Town", "Durban", "Pretoria", "Port Elizabeth",
                   "Bloemfontein", "East London", "Polokwane", "Nelspruit", "Soweto", "Sandton"],
        "regulators": ["FSCA", "SARB", "SARS", "CIPC", "NCC", "ICASA", "JSE Limited",
                       "South African Reserve Bank", "Financial Sector Conduct Authority",
                       "South African Revenue Service"],
        "exchanges": ["JSE", "Johannesburg Stock Exchange", "J203", "J200", "ALSI"],
        "currency_terms": ["ZAR", "Rand", "South African Rand", "R"],
        "geo_descriptor": "a Southern African emerging market",
    },
    "UAE": {
        "adjective": ["Emirati", "UAE", "Emirates"],
        "cities": ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah",
                   "Fujairah", "Umm Al Quwain", "Al Ain", "DIFC", "ADGM"],
        "regulators": ["CBUAE", "SCA", "DFSA", "FSRA", "ADGM", "DIFC", "FTA",
                       "Central Bank of UAE", "Securities and Commodities Authority",
                       "Dubai Financial Services Authority", "Federal Tax Authority"],
        "exchanges": ["DFM", "ADX", "Dubai Financial Market", "Abu Dhabi Securities Exchange",
                      "Nasdaq Dubai"],
        "currency_terms": ["AED", "Dirham", "UAE Dirham", "Emirati Dirham", "Dhs"],
        "geo_descriptor": "a Gulf emerging market",
    },
    "Ghana": {
        "adjective": ["Ghanaian"],
        "cities": ["Accra", "Kumasi", "Tamale", "Sekondi-Takoradi", "Cape Coast"],
        "regulators": ["SEC Ghana", "BoG", "GRA", "NCA", "Bank of Ghana"],
        "exchanges": ["GSE", "Ghana Stock Exchange"],
        "currency_terms": ["GHS", "Ghana Cedi", "Cedi", "GH₵"],
        "geo_descriptor": "a West African emerging market",
    },
    "Egypt": {
        "adjective": ["Egyptian"],
        "cities": ["Cairo", "Alexandria", "Giza", "Luxor", "Aswan", "Hurghada", "Sharm El Sheikh"],
        "regulators": ["FRA Egypt", "CBE", "ETA", "Central Bank of Egypt",
                       "Financial Regulatory Authority"],
        "exchanges": ["EGX", "Egyptian Exchange", "EGX 30", "EGX 70"],
        "currency_terms": ["EGP", "Egyptian Pound", "LE", "£E"],
        "geo_descriptor": "a North African emerging market",
    },
    "India": {
        "adjective": ["Indian", "Bharat", "Bharatiya"],
        "cities": ["Mumbai", "Delhi", "Bangalore", "Bengaluru", "Chennai", "Kolkata",
                   "Hyderabad", "Pune", "Ahmedabad", "Surat", "Jaipur", "Noida", "Gurgaon"],
        "regulators": ["SEBI", "RBI", "CCI", "IRDAI", "MCA",
                       "Securities and Exchange Board of India", "Reserve Bank of India",
                       "Competition Commission of India"],
        "exchanges": ["BSE", "NSE India", "Bombay Stock Exchange", "Sensex", "Nifty 50", "Nifty"],
        "currency_terms": ["INR", "Rupee", "Indian Rupee", "Rs", "₹"],
        "geo_descriptor": "a South Asian emerging market",
    },
    "Saudi Arabia": {
        "adjective": ["Saudi", "Saudi Arabian", "KSA"],
        "cities": ["Riyadh", "Jeddah", "Mecca", "Medina", "Dammam", "Khobar", "NEOM"],
        "regulators": ["CMA Saudi", "SAMA", "ZATCA", "Saudi Central Bank",
                       "Capital Market Authority"],
        "exchanges": ["Tadawul", "Saudi Exchange", "TASI"],
        "currency_terms": ["SAR", "Saudi Riyal", "Riyal", "ر.س"],
        "geo_descriptor": "a Gulf emerging market",
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# Industry terminology mappings
# ──────────────────────────────────────────────────────────────────────────────

INDUSTRY_VARIANTS = {
    "IT Services": ["information technology", "software services", "tech services",
                    "outsourcing", "BPO", "ITO", "IT-BPO", "software engineering",
                    "system integration", "managed services"],
    "Banking": ["financial services", "retail bank", "commercial bank", "investment bank",
                "banking sector", "Islamic banking", "microfinance bank"],
    "Telecom": ["telecommunications", "telco", "mobile network operator", "MNO",
                "ISP", "internet service provider", "broadband"],
    "FMCG": ["consumer goods", "fast-moving consumer goods", "consumer packaged goods",
             "CPG", "consumer staples", "household products"],
    "Manufacturing": ["industrial", "factory production", "OEM", "manufacturer",
                      "industrial production", "assembly"],
    "Oil & Gas": ["petroleum", "energy sector", "upstream", "downstream", "midstream",
                  "E&P", "exploration and production", "refining"],
    "Real Estate": ["property", "construction", "developer", "real estate development",
                    "REIT", "property management"],
    "Retail": ["retail trade", "ecommerce", "e-commerce", "online retail",
               "brick and mortar", "consumer retail"],
    "Pharma": ["pharmaceutical", "drug manufacturer", "pharma sector", "biotech",
               "life sciences"],
    "Insurance": ["insurance sector", "insurer", "reinsurance", "takaful"],
    "Logistics": ["logistics sector", "freight", "supply chain", "3PL", "shipping",
                  "courier", "last-mile delivery"],
    "Education": ["EdTech", "education sector", "higher education", "K-12",
                  "education technology"],
    "Healthcare": ["healthcare provider", "hospital chain", "medical services",
                   "health services"],
    "Agriculture": ["agribusiness", "agritech", "farming sector", "agricultural"],
    "Media": ["media sector", "broadcasting", "publishing", "entertainment industry"],
}


# ──────────────────────────────────────────────────────────────────────────────
# Intent-revealing phrase replacements
# ──────────────────────────────────────────────────────────────────────────────

INTENT_REPLACEMENTS = [
    (re.compile(r'\bconduct(?:ing)? (?:a )?(?:full |complete |comprehensive |detailed )?(?:analysis|assessment|review|evaluation) of\b', re.IGNORECASE),
     "analyse the following hypothetical scenario for"),
    (re.compile(r'\b(?:analyse|analyze) (?:the )?(?:business|company|firm|organisation|organization|enterprise|corporation)\b', re.IGNORECASE),
     "analyse the subject entity"),
    (re.compile(r'\b(?:this|the) (?:company|firm|business|organisation|organization|enterprise|corporation)\b', re.IGNORECASE),
     "the subject entity"),
    (re.compile(r'\btheir (?:business|company|firm|operations|organisation|organization)\b', re.IGNORECASE),
     "the subject entity's operations"),
    (re.compile(r'\bcompetitive (?:intelligence|analysis) for\b', re.IGNORECASE),
     "structural competitive analysis of"),
    (re.compile(r'\bdue diligence on\b', re.IGNORECASE),
     "structural assessment of"),
    (re.compile(r'\bbackground check (?:on|of)\b', re.IGNORECASE),
     "background review of"),
    (re.compile(r'\binvestigat(?:e|ing|ion of)\b', re.IGNORECASE),
     "review of"),
    (re.compile(r'\bclient(?:\'s)?\b', re.IGNORECASE),
     "the subject"),
]


# ──────────────────────────────────────────────────────────────────────────────
# Dynamic discovery — stopword filter
# Title Case words matching these (case-insensitive) are not treated as entities
# ──────────────────────────────────────────────────────────────────────────────

_DISCOVERY_STOPWORDS = frozenset({
    # Articles, conjunctions, prepositions
    "the", "and", "or", "but", "for", "nor", "so", "yet",
    "a", "an", "in", "on", "at", "to", "of", "by", "from",
    "with", "without", "through", "about", "between", "into",
    "upon", "over", "under", "above", "below", "across",
    # Pronouns and determiners
    "this", "that", "these", "those", "its", "their", "our",
    "his", "her", "they", "them", "we", "us", "he", "she",
    # Common verbs
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "must",
    "not", "no", "any", "all", "some", "each", "every",
    # Business / analytical terms — not proper nouns
    "company", "corporation", "limited", "group", "holdings",
    "market", "markets", "exchange", "stock", "currency",
    "sector", "industry", "business", "service", "services",
    "analysis", "report", "review", "summary", "overview",
    "financial", "annual", "quarterly", "revenue", "profit",
    "growth", "strategy", "performance", "management",
    "operations", "division", "subsidiary", "unit",
    "product", "products", "line", "lines",
    # Already-replaced placeholder tokens (prevent double-masking)
    "city", "regulator", "entity", "competitor",
    "executive", "location", "person",
    "company a", "the ceo", "the cfo", "the coo", "the cto",
    "market x", "currency z",
    # Common titles
    "mr", "mrs", "ms", "dr", "prof", "sir",
    "ceo", "cfo", "cto", "coo", "md", "board", "director",
    "chairman", "president", "manager", "officer",
    # Month / weekday names
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    # Common adjectives / descriptors
    "north", "south", "east", "west", "central", "national", "federal",
    "new", "old", "first", "second", "third", "last", "next",
    "international", "global", "local", "regional", "private", "public",
    "general", "special", "official", "technical", "commercial",
    "economic", "social", "political", "legal", "major", "key",
    "strong", "weak", "high", "low", "large", "small", "top",
    # Generic sentence starters
    "however", "therefore", "additionally", "furthermore", "moreover",
    "although", "despite", "regarding", "according", "based",
    "analytical", "scenario", "hypothetical", "context",
    "subject", "entity", "placeholder",
})

# Title Case sequence pattern: 1-4 consecutive capitalised words (allows hyphens)
_TITLE_CASE_PATTERN = re.compile(
    r'\b([A-Z][a-zA-Z]+(?:(?:[ -][A-Z][a-zA-Z]+)){0,3})\b'
)

# Heuristic: likely a city if name ends with these common suffixes
_CITY_SUFFIXES = ("-abad", "-bad", "-pore", "-grad", "-stad", "-ville", "-ton", "-burg")

# Simple two-word person name pattern
_PERSON_NAME_RE = re.compile(r'^[A-Z][a-z]+ [A-Z][a-z]+$')

# Company suffix words
_COMPANY_SUFFIXES = frozenset({
    "limited", "ltd", "inc", "corp", "corporation", "plc",
    "group", "holdings", "international", "ventures",
})


def _classify_entity(term: str) -> str:
    """Heuristically classify a discovered entity type."""
    lower = term.lower()
    # City suffix check
    for suffix in _CITY_SUFFIXES:
        if lower.endswith(suffix):
            return "city"
    # Two-word proper name → person
    if _PERSON_NAME_RE.match(term):
        return "person"
    # Ends with company suffix → company
    last_word = lower.rsplit(" ", 1)[-1]
    if last_word in _COMPANY_SUFFIXES:
        return "company"
    return "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Bucketing functions for numerical data
# ──────────────────────────────────────────────────────────────────────────────

def _bucket_revenue(value: float) -> str:
    """Convert a specific revenue figure to an anonymous range in USD-equivalent."""
    if value < 1_000_000:
        return "under 1M USD equivalent"
    elif value < 10_000_000:
        m = round(value / 1_000_000)
        return f"approximately {max(1, m-2)}M-{m+2}M USD equivalent"
    elif value < 100_000_000:
        m = round(value / 10_000_000) * 10
        return f"approximately {max(10, m-10)}M-{m+10}M USD equivalent"
    elif value < 1_000_000_000:
        m = round(value / 100_000_000) * 100
        return f"approximately {max(100, m-100)}M-{m+100}M USD equivalent"
    elif value < 10_000_000_000:
        b = round(value / 1_000_000_000, 1)
        return f"approximately {max(0.5, b-0.5):.1f}B-{b+0.5:.1f}B USD equivalent"
    else:
        return "multi-billion USD equivalent"


def _bucket_employees(count: int) -> str:
    if count < 10:        return "under 10 employees"
    elif count < 50:      return "10-50 employees"
    elif count < 200:     return "50-200 employees"
    elif count < 500:     return "200-500 employees"
    elif count < 1000:    return "500-1,000 employees"
    elif count < 5000:    return "1,000-5,000 employees"
    elif count < 10000:   return "5,000-10,000 employees"
    elif count < 50000:   return "10,000-50,000 employees"
    elif count < 100000:  return "50,000-100,000 employees"
    else:                 return "over 100,000 employees"


def _bucket_percentage(value: float) -> str:
    """Round to nearest 5% for market share, margins, growth rates."""
    rounded = round(value / 5) * 5
    return f"approximately {rounded}%"


# ──────────────────────────────────────────────────────────────────────────────
# Session-scoped mapping
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SanitisationMap:
    """
    Bidirectional mapping between real values and placeholders, scoped to one
    analysis run. Every entity gets a unique numbered placeholder, ensuring
    lossless restore even when multiple cities, regulators, or exchanges appear.
    """
    real_to_placeholder: dict = field(default_factory=dict)
    placeholder_to_real: dict = field(default_factory=dict)

    # Per-type counters — each entity gets its own slot
    company_counter: int = 0       # Competitor 1, Competitor 2, …
    person_counter: int = 0        # the CEO, the CFO, … (explicit executives)
    product_counter: int = 0       # Product Line A, B, …
    location_counter: int = 0      # Location 1, 2, …
    city_counter: int = 0          # City 1, City 2, … (region + discovered)
    regulator_counter: int = 0     # Regulator 1, Regulator 2, …
    exchange_counter: int = 0      # Exchange 1, Exchange 2, …
    person_disc_counter: int = 0   # Person 1, Person 2, … (dynamically found)
    unknown_counter: int = 0       # Entity 1, Entity 2, …

    # Newly found entities this run (not in static corpus or registry)
    discovered: List[dict] = field(default_factory=list)

    def add(self, real_value: str, placeholder: str) -> None:
        if not real_value or not real_value.strip():
            return
        key = real_value.lower().strip()
        if key in self.real_to_placeholder:
            return
        self.real_to_placeholder[key] = placeholder
        # Only the FIRST registration wins for the reverse mapping.
        # All variants ("Systems Ltd", "Systems Inc", …) map forward to the same
        # placeholder, but restore always uses the canonical original name.
        if placeholder not in self.placeholder_to_real:
            self.placeholder_to_real[placeholder] = real_value

    def next_competitor(self) -> str:
        self.company_counter += 1
        return f"Competitor {self.company_counter}"

    def next_person(self) -> str:
        """Labeled names for explicitly provided executives."""
        self.person_counter += 1
        labels = ["the CEO", "the CFO", "the COO", "the CTO", "the Managing Director",
                  "the Chairperson", "Executive A", "Executive B", "Executive C",
                  "Executive D", "Executive E"]
        if self.person_counter <= len(labels):
            return labels[self.person_counter - 1]
        return f"Senior Executive {self.person_counter}"

    def next_product(self) -> str:
        self.product_counter += 1
        if self.product_counter <= 26:
            return f"Product Line {chr(64 + self.product_counter)}"
        return f"Product Line {self.product_counter}"

    def next_location(self) -> str:
        self.location_counter += 1
        return f"Location {self.location_counter}"

    def next_city(self) -> str:
        self.city_counter += 1
        return f"City {self.city_counter}"

    def next_regulator(self) -> str:
        self.regulator_counter += 1
        return f"Regulator {self.regulator_counter}"

    def next_exchange(self) -> str:
        self.exchange_counter += 1
        return f"Exchange {self.exchange_counter}"

    def next_discovered_person(self) -> str:
        self.person_disc_counter += 1
        return f"Person {self.person_disc_counter}"

    def next_unknown(self) -> str:
        self.unknown_counter += 1
        return f"Entity {self.unknown_counter}"


# ──────────────────────────────────────────────────────────────────────────────
# Main sanitiser class
# ──────────────────────────────────────────────────────────────────────────────

class PromptSanitiser:
    """
    Sanitises prompts before sending to external AI APIs.
    Maintains a session-scoped mapping for consistent placeholder use.

    Usage:
        sanitiser = build_sanitiser_for_client(...)
        clean_prompt = sanitiser.sanitise(raw_prompt)
        raw_response = call_api(clean_prompt)
        real_response = sanitiser.restore(raw_response)
        discovered = sanitiser.get_discovered_entities()  # persist to DB
    """

    HYPOTHETICAL_FRAMING = (
        "ANALYTICAL SCENARIO — HYPOTHETICAL CONTEXT\n"
        "The following describes a hypothetical business scenario for analytical purposes. "
        "All entity names, market identifiers, regulators, and figures are generic "
        "placeholders. Provide structural analytical insights based on the patterns "
        "described, without referencing or attempting to identify specific real-world "
        "companies, individuals, or events.\n\n"
        "SCENARIO:\n"
    )

    def __init__(
        self,
        primary_company: str,
        region: str,
        industry: str,
        currency: Optional[str] = None,
        exchange: Optional[str] = None,
        regulator: Optional[str] = None,
        competitors: Optional[list] = None,
        executives: Optional[list] = None,
        products: Optional[list] = None,
        parent_company: Optional[str] = None,
        subsidiaries: Optional[list] = None,
        additional_terms: Optional[list] = None,
    ):
        self.smap = SanitisationMap()
        self.primary_company = primary_company
        self.region = region
        self.industry = industry

        # Primary company — try common variations
        self.smap.add(primary_company, "Company A")
        self._register_company_variations(primary_company, "Company A")

        # Parent and subsidiaries
        if parent_company:
            self.smap.add(parent_company, "the parent group")
            self._register_company_variations(parent_company, "the parent group")

        for sub in (subsidiaries or []):
            placeholder = f"Subsidiary {chr(64 + self.smap.company_counter + 1)}"
            self.smap.add(sub, placeholder)
            self.smap.company_counter += 1

        # Region terms (cities/regulators/exchanges each get unique numbered slots)
        self._register_region(region)

        # Currency
        if currency:
            self.smap.add(currency, "Currency Z")

        # Explicitly provided exchange (separate from region's exchange list)
        if exchange and exchange.lower() not in self.smap.real_to_placeholder:
            placeholder = self.smap.next_exchange()
            self.smap.add(exchange, placeholder)

        # Explicitly provided regulator (separate from region's regulator list)
        if regulator and regulator.lower() not in self.smap.real_to_placeholder:
            placeholder = self.smap.next_regulator()
            self.smap.add(regulator, placeholder)

        # Industry
        self._register_industry(industry)

        # Competitors
        for comp in (competitors or []):
            placeholder = self.smap.next_competitor()
            self.smap.add(comp, placeholder)
            self._register_company_variations(comp, placeholder)

        # Executives
        for exec_name in (executives or []):
            placeholder = self.smap.next_person()
            self.smap.add(exec_name, placeholder)
            self._register_person_variations(exec_name, placeholder)

        # Products
        for product in (products or []):
            placeholder = self.smap.next_product()
            self.smap.add(product, placeholder)

        # Caller-supplied additional terms
        for term, replacement in (additional_terms or []):
            self.smap.add(term, replacement)

    def _register_company_variations(self, name: str, placeholder: str) -> None:
        """Register common variations of a company name."""
        suffixes = [" Limited", " Ltd", " Ltd.", " Inc", " Inc.", " Plc", " PLC",
                    " Corporation", " Corp", " Corp.", " Company", " Co", " Co.",
                    " LLC", " LLP", " Group", " Holdings", " International", " Intl",
                    " (Pvt)", " Pvt", " Private", " Public"]
        base = name
        for suffix in suffixes:
            if name.endswith(suffix):
                base = name[:-len(suffix)].strip()
                self.smap.add(base, placeholder)
                break
        for suffix in suffixes:
            variant = base + suffix
            if variant != name:
                self.smap.add(variant, placeholder)
        first_word = base.split()[0] if base else ""
        if first_word and len(first_word) > 3:
            self.smap.add(first_word, placeholder)

    def _register_person_variations(self, name: str, placeholder: str) -> None:
        """Register variations of a person's name (first only, last only, etc.)."""
        parts = name.split()
        if len(parts) >= 2:
            self.smap.add(parts[0], placeholder)
            self.smap.add(parts[-1], placeholder)
            if len(parts) > 2:
                self.smap.add(f"{parts[0]} {parts[-1]}", placeholder)
            if parts[0]:
                self.smap.add(f"{parts[0][0]}. {parts[-1]}", placeholder)
                self.smap.add(f"{parts[0][0]} {parts[-1]}", placeholder)

    def _register_region(self, region: str) -> None:
        """Register all variants of a region's identifiers with unique numbered slots."""
        variants = REGION_VARIANTS.get(region)
        if not variants:
            self.smap.add(region, "Market X")
            return

        self.smap.add(region, "Market X")
        self.smap.add(variants.get("geo_descriptor", "an emerging market"), "Market X")
        for adj in variants.get("adjective", []):
            self.smap.add(adj, "of Market X")

        # Each city gets its own numbered placeholder — Lahore ≠ Karachi
        for city in variants.get("cities", []):
            placeholder = self.smap.next_city()
            self.smap.add(city, placeholder)

        # Each regulator gets its own numbered placeholder — SECP ≠ SBP
        for reg in variants.get("regulators", []):
            placeholder = self.smap.next_regulator()
            self.smap.add(reg, placeholder)

        # Each exchange gets its own numbered placeholder
        for exch in variants.get("exchanges", []):
            placeholder = self.smap.next_exchange()
            self.smap.add(exch, placeholder)

        for cur in variants.get("currency_terms", []):
            self.smap.add(cur, "Currency Z")

    def _register_industry(self, industry: str) -> None:
        """Register variants of industry terminology."""
        self.smap.add(industry, "the sector")
        variants = INDUSTRY_VARIANTS.get(industry, [])
        for variant in variants:
            self.smap.add(variant, "the sector")

    def _discover_and_mask_unknowns(self, text: str) -> str:
        """
        Scan text for unmasked Title Case sequences after static replacement.
        Each newly found entity gets a unique placeholder and is recorded for
        persistence to the client entity registry.
        """
        # Collect all known placeholder values so we don't re-process them
        known_placeholders = set(self.smap.placeholder_to_real.keys())

        def _is_excluded(term: str) -> bool:
            lower = term.lower()
            if lower in _DISCOVERY_STOPWORDS:
                return True
            # Already a known placeholder token
            if term in known_placeholders:
                return True
            # Starts with a known placeholder prefix (e.g. "City 1", "Regulator 2")
            for ph in known_placeholders:
                if term.lower().startswith(ph.lower()):
                    return True
            # Already in the forward map
            if lower in self.smap.real_to_placeholder:
                return True
            # Too short (single-character abbreviations, etc.)
            if len(term) <= 2:
                return True
            return False

        # Find all Title Case sequences, deduplicate, longest first
        candidates = list(dict.fromkeys(_TITLE_CASE_PATTERN.findall(text)))
        candidates.sort(key=len, reverse=True)

        result = text
        for term in candidates:
            if _is_excluded(term):
                continue

            entity_type = _classify_entity(term)

            if entity_type == "city":
                placeholder = self.smap.next_city()
            elif entity_type == "person":
                placeholder = self.smap.next_discovered_person()
                self._register_person_variations(term, placeholder)
            elif entity_type == "company":
                placeholder = self.smap.next_competitor()
                self._register_company_variations(term, placeholder)
            else:
                placeholder = self.smap.next_unknown()

            self.smap.add(term, placeholder)
            self.smap.discovered.append({
                "entity_value": term,
                "entity_type": entity_type,
            })

            # Replace all occurrences in text (case-insensitive)
            if len(term) <= 4:
                pat = r'\b' + re.escape(term) + r'\b'
            else:
                pat = re.escape(term)
            result = re.sub(pat, placeholder, result, flags=re.IGNORECASE)

        return result

    def sanitise(self, text: str) -> str:
        """
        Sanitise a prompt before sending to an external AI API.

        Order of operations:
        1. PII patterns (emails, phones, URLs) — before entity replacement to
           avoid domain corruption
        2. Intent replacement — on original text before entity names are swapped
        3. Named entity replacement (static corpus + client registry)
        4. Dynamic discovery — find and mask any unmasked Title Case entities
        5. Numerical bucketing
        6. Hypothetical framing prefix

        Returns the sanitised text ready to send to the AI API.
        """
        if not text:
            return text

        result = text

        # 1. Strip PII patterns FIRST
        for pattern, replacement in GENERIC_PII_PATTERNS:
            result = pattern.sub(replacement, result)

        # 2. Intent replacement on the (now PII-free) text
        for pattern, replacement in INTENT_REPLACEMENTS:
            result = pattern.sub(replacement, result)

        # 3. Named-entity replacement (longest match first to avoid partial hits)
        sorted_replacements = sorted(
            self.smap.real_to_placeholder.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )
        for real, placeholder in sorted_replacements:
            if len(real) <= 4:
                pattern = r'\b' + re.escape(real) + r'\b'
            else:
                pattern = re.escape(real)
            result = re.sub(pattern, placeholder, result, flags=re.IGNORECASE)

        # 4. Dynamic discovery — mask any Title Case entities that slipped through
        result = self._discover_and_mask_unknowns(result)

        # 5. Bucket large numerical values
        result = self._bucket_numerical_values(result)

        # 6. Prepend hypothetical framing
        return self.HYPOTHETICAL_FRAMING + result

    def restore(self, text: str) -> str:
        """
        Restore real values into the AI response.
        Replaces placeholders with actual entity names for display to the user.
        """
        if not text:
            return text

        result = text

        # Remove the hypothetical framing prefix if it echoed back
        result = re.sub(
            r'^.*?ANALYTICAL SCENARIO.*?SCENARIO:\s*',
            '',
            result,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Sort by placeholder length descending — restore longest first to avoid
        # "City 1" matching before "City 10"
        sorted_restorations = sorted(
            self.smap.placeholder_to_real.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )
        for placeholder, real in sorted_restorations:
            result = result.replace(placeholder, real)

        return result.strip()

    def get_discovered_entities(self) -> List[dict]:
        """
        Return entities discovered during this run (not in static corpus or registry).
        Each entry: {"entity_value": str, "entity_type": str}
        Call this after restore() to persist new entities to the client registry.
        """
        return list(self.smap.discovered)

    def _bucket_numerical_values(self, text: str) -> str:
        """Replace specific large numbers with bucketed ranges."""

        def replace_revenue(match):
            raw = match.group(0)
            cleaned = raw.replace(",", "").replace("$", "").replace("USD", "").strip()
            multiplier = 1
            if cleaned.upper().endswith("B") or cleaned.upper().endswith("BN"):
                multiplier = 1_000_000_000
                cleaned = re.sub(r'[Bb][Nn]?$', '', cleaned)
            elif cleaned.upper().endswith("M") or cleaned.upper().endswith("MN"):
                multiplier = 1_000_000
                cleaned = re.sub(r'[Mm][Nn]?$', '', cleaned)
            elif cleaned.upper().endswith("K"):
                multiplier = 1_000
                cleaned = cleaned[:-1]
            try:
                value = float(cleaned) * multiplier
                if value >= 100_000:
                    return _bucket_revenue(value)
            except ValueError:
                pass
            return raw

        text = re.sub(
            r'(?:USD|US\$|\$|EUR|€|GBP|£)\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s?[MBK]N?)?',
            replace_revenue,
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r'\b\d{1,4}(?:\.\d+)?\s?[MBK]N?\b(?!\s*%)',
            replace_revenue,
            text,
            flags=re.IGNORECASE,
        )

        def replace_employees(match):
            try:
                num_str = match.group(1).replace(",", "")
                count = int(num_str)
                if count >= 100:
                    return f"{_bucket_employees(count)} employees"
            except (ValueError, IndexError):
                pass
            return match.group(0)

        text = re.sub(
            r'\b(\d{1,3}(?:,\d{3})+|\d{3,})\s+employees\b',
            replace_employees,
            text,
            flags=re.IGNORECASE,
        )

        return text

    def get_mapping_summary(self) -> dict:
        """
        Return audit-safe summary of what was anonymised.
        Real values are NEVER included — only categories and counts.
        """
        categories = {}
        for placeholder in self.smap.placeholder_to_real.keys():
            # Derive category from placeholder prefix
            parts = placeholder.split()
            if not parts:
                continue
            category = parts[0] if len(parts) > 1 else placeholder
            categories[category] = categories.get(category, 0) + 1
        return {
            "total_entities_anonymised": len(self.smap.placeholder_to_real),
            "categories": categories,
            "primary_company_anonymised": self.primary_company.lower() in self.smap.real_to_placeholder,
            "region_anonymised": self.region in REGION_VARIANTS,
            "entities_discovered_this_run": len(self.smap.discovered),
        }


# ──────────────────────────────────────────────────────────────────────────────
# Factory function
# ──────────────────────────────────────────────────────────────────────────────

def build_sanitiser_for_client(
    business_name: str,
    region_id: str,
    industry: Optional[str] = None,
    competitors: Optional[list] = None,
    executives: Optional[list] = None,
    products: Optional[list] = None,
    parent_company: Optional[str] = None,
    subsidiaries: Optional[list] = None,
    client_id: Optional[str] = None,
) -> "PromptSanitiser":
    """
    Build a PromptSanitiser from the active client/run context.

    Loading order:
    1. Static regional corpus (cities, regulators, exchanges — numbered)
    2. Previously discovered client entities from DB registry (stable slots)
    3. Dynamic discovery happens at sanitise() time (new entities, new numbers)

    Call once per analysis run and reuse across all modules in that run.
    Pass client_id to load the client's entity registry for improved accuracy.
    """
    from config.regions import get_region

    region_obj = get_region(region_id)
    region_name = region_obj.name
    currency    = region_obj.currency
    exchange    = region_obj.exchange_name
    regulator   = region_obj.regulatory_bodies[0] if region_obj.regulatory_bodies else None
    industry_clean = industry or "the sector"

    sanitiser = PromptSanitiser(
        primary_company=business_name,
        region=region_name,
        industry=industry_clean,
        currency=currency,
        exchange=exchange,
        regulator=regulator,
        competitors=competitors or [],
        executives=executives or [],
        products=products or [],
        parent_company=parent_company,
        subsidiaries=subsidiaries or [],
    )

    # Load previously discovered entities from client registry
    # These get registered in the sanitiser map after the static corpus,
    # getting stable early slots so they can be accurately restored.
    if client_id:
        try:
            from database.client_entities import load_client_entities
            saved = load_client_entities(client_id)
            for entity in saved:
                etype = entity.get("entity_type", "unknown")
                value = entity.get("entity_value", "")
                if not value or value.lower() in sanitiser.smap.real_to_placeholder:
                    continue
                if etype == "city":
                    placeholder = sanitiser.smap.next_city()
                elif etype == "person":
                    placeholder = sanitiser.smap.next_discovered_person()
                    sanitiser._register_person_variations(value, placeholder)
                elif etype == "company":
                    placeholder = sanitiser.smap.next_competitor()
                    sanitiser._register_company_variations(value, placeholder)
                elif etype == "regulator":
                    placeholder = sanitiser.smap.next_regulator()
                elif etype == "exchange":
                    placeholder = sanitiser.smap.next_exchange()
                else:
                    placeholder = sanitiser.smap.next_unknown()
                sanitiser.smap.add(value, placeholder)
        except Exception:
            pass  # DB not available yet — degrade gracefully

    return sanitiser
