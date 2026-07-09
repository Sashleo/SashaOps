"""
intelligence/compliance_rules.py

Pakistan Regulatory Compliance Knowledge Base.
Static, deterministic, requires no scraping or AI. Used by the Regulatory Risk
module (and PESTLE / Financial context) to assess any PSX-listed or unlisted
Pakistani company against applicable laws and regulations.

Sources: Companies Act 2017, SECP Code of Corporate Governance Regulations 2017,
Banking Companies Ordinance 1962, SBP Prudential Regulations, DRAP Act 2012 /
Drugs Act 1976, NEPRA Act 1997, OGRA Ordinance 2002, PTA Act 1996, Income Tax
Ordinance 2001 / Sales Tax Act 1990, Environmental Protection Act 1997,
Anti-Money Laundering Act 2010, FATF framework.
"""

from __future__ import annotations
from typing import Optional


# ── Universal obligations — every PSX-listed company ─────────────────────────
UNIVERSAL_LISTED_OBLIGATIONS = {
    "regulator": "SECP + PSX",
    "governing_laws": [
        "Companies Act 2017 (Act XIX of 2017)",
        "Companies Regulations 2024 (updated April 2026)",
        "Listed Companies (Code of Corporate Governance) Regulations 2017",
        "Securities Act 2015",
        "PSX Rulebook",
        "Income Tax Ordinance 2001",
        "Sales Tax Act 1990",
        "Anti-Money Laundering Act 2010",
        "Environmental Protection Act 1997",
    ],
    "mandatory_filings": [
        "Annual audited financial statements filed with SECP within 4 months of fiscal year end",
        "Quarterly unaudited financial results disclosed to PSX within 30 days of quarter end",
        "Annual return filed with SECP within 60 days of AGM",
        "Form 9 (director/officer changes) filed within 15 days of change",
        "Beneficial ownership declarations filed via SECP e-Services portal; updated on any change",
        "Pattern of shareholding disclosed with annual report",
        "AGM held within 4 months of fiscal year end",
        "Board meetings: minimum 4 per year, quorum majority including at least one independent director",
    ],
    "corporate_governance": [
        "Minimum 1/3 independent directors on board (paid-up capital > PKR 1B)",
        "Audit committee mandatory: minimum 3 members, majority independent, chaired by independent director",
        "HR and Remuneration Committee mandatory for listed companies",
        "CFO, Internal Auditor and Company Secretary must meet SECP qualification requirements",
        "External auditor must be registered with the Audit Oversight Board of Pakistan (AOB)",
        "CEO and Chairman roles must be held by separate individuals",
        "Statement of Compliance with Code of Corporate Governance required in annual report",
        "ESG disclosure guidelines phased in for listed entities (SECP 2026 framework)",
    ],
    "key_risks": [
        "Late statutory filings → SECP penalties and CREST notices",
        "Related-party transactions not at arm's length → governance red flag",
        "Auditor qualification/emphasis of matter → disclosure and confidence risk",
        "AML/FATF gaps → enhanced scrutiny on cross-border and cash transactions",
    ],
    "tax_obligations": [
        "Corporate income tax: 29% for listed companies (vs 33% unlisted)",
        "Super tax on higher income bands per Finance Acts",
        "Withholding tax collection/deposit on dividends, salaries, contracts",
        "Sales tax registration and monthly returns where applicable",
    ],
}


# ── Universal obligations — unlisted / private companies ──────────────────────
UNIVERSAL_UNLISTED_OBLIGATIONS = {
    "regulator": "SECP",
    "governing_laws": [
        "Companies Act 2017 (Act XIX of 2017)",
        "Companies Regulations 2024",
        "Income Tax Ordinance 2001",
        "Sales Tax Act 1990",
        "Anti-Money Laundering Act 2010",
        "Environmental Protection Act 1997",
        "Employees Old-Age Benefits Act 1976 (EOBI)",
        "Workers Welfare Fund Ordinance 1971",
    ],
    "mandatory_filings": [
        "Annual return filed with SECP within 60 days of AGM",
        "Annual audited financial statements filed with SECP if paid-up capital > PKR 10M",
        "Unaudited financials acceptable if paid-up capital ≤ PKR 1M (single member company exemption)",
        "Form 9 filed within 15 days of any change in directors or officers",
        "Beneficial ownership declaration via SECP e-Services portal; updated on any change",
        "AGM: private companies exempt from many AGM requirements but must hold if articles require",
        "Tax returns: annual income tax return filed with FBR by December 31",
        "Sales tax return: monthly filing if registered for sales tax",
        "Withholding tax: monthly deposit and annual reconciliation",
    ],
    "corporate_governance": [
        "Private companies: no mandatory independent director requirement",
        "Audit committee: not mandatory for private companies",
        "Single member companies: exempt from audit, AGM, company secretary requirements",
        "Private companies with paid-up capital < PKR 10M: no mandatory audit",
        "Private companies with paid-up capital PKR 10M-100M: audit required but no SECP filing required",
        "Private companies with paid-up capital > PKR 100M: full audit and SECP filing required",
        "No mandatory CFO qualification requirements (unlike listed companies)",
        "Related party transactions: must be at arm's length and disclosed in financial statements",
        "Beneficial ownership: mandatory declaration regardless of company size (SECP 2026 amendment)",
    ],
    "tax_obligations": [
        "Corporate income tax: 33% standard rate for private companies (vs 29% for listed)",
        "Super tax: applicable on high-income entities per Finance Acts",
        "Active taxpayer status: must appear on FBR Active Taxpayer List (ATL)",
        "WHT compliance: collect and deposit withholding tax on salaries, contracts, dividends",
        "Sales tax: register if annual turnover exceeds PKR 10M; monthly return thereafter",
        "Provincial services tax: register with relevant PRA/SRB/KPRA/BRA if providing services",
        "EOBI: 5% employer contribution on minimum wages for all permanent employees",
        "WWF: 2% of taxable income above PKR 500,000",
    ],
    "key_risks": [
        "Non-appearance on FBR Active Taxpayer List → higher withholding rates",
        "Unaudited/late filings → SECP penalties on larger private companies",
        "Related-party transactions not at arm's length → tax and governance exposure",
        "AML scrutiny for high-cash / real-estate private businesses",
    ],
    "aml_obligations": [
        "Anti-Money Laundering Act 2010 applies to all companies",
        "Real estate and high-cash businesses face enhanced AML scrutiny",
        "Large cash transactions: reportable thresholds apply",
        "FATF grey-listing context: cross-border transactions face enhanced scrutiny",
    ],
}


# ── Sector-specific compliance ────────────────────────────────────────────────
SECTOR_COMPLIANCE = {

    "banking": {
        "regulator": "State Bank of Pakistan (SBP)",
        "governing_laws": [
            "Banking Companies Ordinance 1962",
            "State Bank of Pakistan Act 1956",
            "SBP Prudential Regulations for Corporate/Commercial Banking (updated June 2024)",
            "Anti-Money Laundering Act 2010",
            "Financial Institutions (Recovery of Finances) Ordinance 2001",
            "Payment Systems and Electronic Fund Transfers Act 2007",
            "Foreign Exchange Regulation Act 1947",
        ],
        "compliance_checklist": [
            "Capital Adequacy Ratio (CAR): minimum 10% under Basel III (Tier 1 + Tier 2)",
            "Common Equity Tier 1 (CET1): minimum 6% of risk-weighted assets",
            "Minimum Capital Requirement (MCR): PKR 10B for commercial banks",
            "Non-Performing Loans (NPL): classification at 90+ days overdue",
            "Provisioning: 20% Substandard, 50% Doubtful, 100% Loss categories",
            "Advance-to-Deposit Ratio (ADR): tax penalty if ADR exceeds 50% threshold",
            "Single Obligor Limit: exposure ≤ 25% of bank equity to any one party",
            "SBP prior approval required for CEO/President appointment or resignation",
            "AML/CFT: enhanced due diligence for high-risk customers and jurisdictions",
            "Digital banking / Islamic banking window: separate SBP approval required",
            "Annual SBP inspection: access and audit trail must be maintained",
        ],
        "key_risks": [
            "NPL deterioration → higher provisioning → earnings pressure",
            "CAR breach → SBP restrictions on dividends, growth and new licences",
            "ADR above 50% threshold → additional tax on interest income",
            "KIBOR/policy-rate sensitivity on the variable-rate loan book",
            "Circular-debt exposure via power-sector lending",
            "FATF scrutiny on cross-border payments",
        ],
        "financial_signals_to_assess": [
            "Net Interest Margin (NIM): positive and stable",
            "Cost-to-Income Ratio: below 60% generally healthy",
            "NPL coverage ratio: provisions/NPLs > 70% preferred",
            "CASA ratio: higher = lower cost of funds",
            "ADR: monitor against the 50% tax threshold",
            "ROE: listed Pakistani banks averaged 15–25% recently",
        ],
    },

    "pharmaceuticals": {
        "regulator": "Drug Regulatory Authority of Pakistan (DRAP)",
        "governing_laws": [
            "DRAP Act 2012",
            "Drugs Act 1976",
            "Drugs (Licensing, Registering and Advertising) Rules 1976",
            "Pharmacovigilance Rules 2022",
            "Drug Pricing Policy 2018",
            "DRAP e-portal regulations (mandatory from November 2024)",
        ],
        "compliance_checklist": [
            "Manufacturing license: Form-1 from Central Licensing Board; renewal every 5 years",
            "All products must be registered with DRAP before marketing (product registration / MA)",
            "Good Manufacturing Practice (GMP) certification: mandatory, DRAP inspection",
            "Import license: Form-6 with foreign GMP certificate or WHO prequalification",
            "Drug price increases: prior DRAP approval under Drug Pricing Policy 2018",
            "Pharmacovigilance: Qualified Person for Pharmacovigilance (QPPV) mandatory",
            "Adverse Drug Reaction reporting: serious cases within 15 days to DRAP",
            "Serialisation: 2D DataMatrix barcodes on secondary packaging (SRO 470/2017)",
            "Record retention: manufacturing logs, QC reports, distribution - 5 years minimum",
            "API imports: Form-8 licensing required",
            "Labelling: bilingual (Urdu + English) per DRAP guidelines",
        ],
        "key_risks": [
            "Price control: DRAP caps on essential medicines limit margin expansion",
            "API import cost exposure: PKR depreciation raises raw-material costs",
            "DRAP registration delays: new product launches delayed months to years",
            "GMP inspection failure: can suspend manufacturing license",
            "Serialisation non-compliance: product bans and regulatory action",
        ],
        "financial_signals_to_assess": [
            "Gross margin: pharma typically 35–55%; compression signals API cost or price control",
            "R&D as % of revenue: pipeline investment vs generic focus",
            "Revenue concentration: single-product dependency increases regulatory risk",
            "Receivables days: government/institution receivables can be 120–180+ days",
        ],
    },

    "cement": {
        "regulator": "SECP (corporate) + Competition Commission of Pakistan (CCP) + provincial EPAs",
        "governing_laws": [
            "Companies Act 2017", "Competition Act 2010",
            "Environmental Protection Act 1997",
            "National Environmental Quality Standards (NEQS)",
            "Factories Act 1934",
        ],
        "compliance_checklist": [
            "No-Objection Certificate (NOC) from provincial EPA for plant operations",
            "NEQS for dust, particulate matter, SO2, NOx emissions",
            "Continuous Emission Monitoring Systems (CEMS) for large plants",
            "Annual environmental audit mandatory",
            "Price coordination prohibited under Competition Act 2010",
            "Worker safety: Factories Act 1934 - PPE, ventilation, dust control",
            "EOBI and ESSI social-security contributions",
        ],
        "key_risks": [
            "Coal price volatility: South African coal is primary fuel; global prices hit margins",
            "Energy costs: gas and electricity tariff increases affect kiln operations",
            "PSDP cuts: government development spending drives local demand",
            "Cartel risk: CCP has previously fined the sector for price coordination",
            "Capacity oversupply compresses retention prices",
        ],
        "financial_signals_to_assess": [
            "Gross margin: typically 20–35%; compression indicates coal/energy pressure",
            "Capacity utilization: below 60% is a distress signal",
            "Coal cost as % of revenue: key margin driver",
            "Export revenue %: diversification reduces domestic cyclicality",
        ],
    },

    "oil_gas": {
        "regulator": "Oil and Gas Regulatory Authority (OGRA) + Ministry of Petroleum",
        "governing_laws": [
            "OGRA Ordinance 2002",
            "Petroleum (Exploration and Production) Policy 2012 / Petroleum Policy 2023",
            "Natural Gas (Development Surcharge) Ordinance 1967",
            "Oil and Gas (Safety in Drilling and Production) Regulations 1974",
            "Environmental Protection Act 1997",
        ],
        "compliance_checklist": [
            "Exploration license: DGPC grant required",
            "Petroleum concession agreement with government for exploration blocks",
            "Wellhead gas price determined by OGRA",
            "Development and Production Lease (DPL) separate from exploration license",
            "Environmental Impact Assessment (EIA) mandatory before drilling",
            "HSE management system mandatory per drilling regulations",
            "Royalty and gas development surcharge remitted per ordinance",
            "Annual production reports filed with DGPC",
        ],
        "key_risks": [
            "Circular debt: receivables from SNGPL/SSGCL subject to delayed recovery",
            "OGRA-determined wellhead prices may not reflect market rates",
            "Exploration risk: dry wells write off capitalized costs",
            "Reserve depletion: reserve-replacement ratio is critical",
            "Crude-price exposure on upstream revenue",
        ],
        "financial_signals_to_assess": [
            "Reserve life index: <5 years is a distress signal",
            "EBITDA margin: E&P typically 60–75% in productive periods",
            "Receivables from SNGPL/SSGCL: high balance = circular-debt exposure",
            "Production volume trend from mature fields",
        ],
    },

    "fertilizer": {
        "regulator": "Ministry of Industries + SECP + provincial agriculture departments",
        "governing_laws": [
            "Fertilizers (Control) Act 1977", "Companies Act 2017",
            "OGRA Ordinance 2002 (gas input pricing)",
            "Environmental Protection Act 1997",
        ],
        "compliance_checklist": [
            "Fertilizer manufacturing license from Ministry of Industries",
            "Feed gas supply agreement with SNGPL/SSGCL",
            "Government feed/fuel gas allocation (supply not guaranteed)",
            "Urea retail price increases require MoI notification",
            "Quality standards per government specifications",
            "Environmental compliance: air/water discharge per NEQS",
            "EOBI and ESSI contributions",
        ],
        "key_risks": [
            "Gas supply curtailment directly determines production capacity",
            "Feed-gas subsidy reduction hits margins directly",
            "Urea offtake tied to kharif/rabi crop seasons (monsoon-dependent)",
            "Government price controls limit margin recovery",
            "Delayed subsidy reimbursements (circular debt)",
        ],
        "financial_signals_to_assess": [
            "Urea offtake volumes vs seasonal norms",
            "Gas cost as % of revenue: subsidised-rate changes flow to margins",
            "EBITDA margin: typically 30–50% with adequate gas",
            "Days inventory outstanding: urea buildup signals demand weakness",
        ],
    },

    "power": {
        "regulator": "National Electric Power Regulatory Authority (NEPRA)",
        "governing_laws": [
            "NEPRA Act 1997", "Electricity Act 1910",
            "Performance Standards (Distribution) Rules 2005",
            "NEPRA (Licensing) Regulations 2000",
            "NEPRA Net Metering Regulations 2015",
        ],
        "compliance_checklist": [
            "Generation license: NEPRA license for all plants above 1 MW",
            "Tariff determined by NEPRA; no change without approval",
            "Power Purchase Agreement (PPA) with CPPA-G",
            "Capacity payments received for installed capacity (per PPA structure)",
            "EIA required before commissioning",
            "Grid code / NPCC compliance",
            "Annual performance reporting via NEPRA data portal",
            "AT&C loss-reduction targets set by NEPRA",
        ],
        "key_risks": [
            "Circular debt: delayed payments from CPPA-G/DISCOs (trillions sector-wide)",
            "Capacity-payment risk where dispatch is low",
            "Fuel cost pass-through timing creates cash-flow gaps",
            "Tariff-revision delays lag cost changes",
            "USD-denominated capacity payments vs local-currency revenue",
        ],
        "financial_signals_to_assess": [
            "Capacity utilization (dispatch rate): low dispatch signals grid/PPA issues",
            "Receivables from CPPA-G: high balance = circular-debt exposure",
            "EBITDA margin: typically 15–30% for operating IPPs",
            "Net debt / EBITDA: IPPs carry significant project-level debt",
        ],
    },

    "textile": {
        "regulator": "Ministry of Textile Industry + FBR (export rebates) + SECP",
        "governing_laws": [
            "Textile Policy 2020-25", "Customs Act 1969",
            "Sales Tax Act 1990", "Income Tax Ordinance 2001",
            "Environmental Protection Act 1997 (effluent discharge)",
            "Factories Act 1934",
        ],
        "compliance_checklist": [
            "DLTL (Drawback of Local Taxes and Levies) export rebate on eligible exports",
            "EFS (Export Finance Scheme) / LTFF compliance for export finance",
            "GSP+ status (EU): requires ILO convention compliance",
            "Concessional energy tariff for export-oriented units",
            "Sales tax zero-rating on textile exports",
            "Effluent treatment plant (ETP) mandatory for wet processing; NEQS compliance",
            "ILO conventions (no child labour, worker safety) for EU market access",
            "Social audit required by international buyers",
            "Fire-safety certification (provincial, post-Baldia)",
        ],
        "key_risks": [
            "Energy cost hikes compress margins for energy-intensive spinning",
            "Raw cotton spot rates drive input cost",
            "DLTL refund delays strain working capital",
            "PKR depreciation helps exporters but raises imported fibre/machinery cost",
            "GSP+ revocation risk on ILO non-compliance",
            "Buyer concentration → pricing power over suppliers",
        ],
        "financial_signals_to_assess": [
            "Export revenue as % of total",
            "Gross margin: spinning 8–15%, weaving 10–18%, garments 20–35%",
            "Receivables days: buyer credit typically 60–90 days",
            "Energy cost as % of revenue",
            "DLTL receivable balance signals refund-delay risk",
        ],
    },

    "technology": {
        "regulator": "SECP + PSEB (Pakistan Software Export Board) + PTA (if telecom-adjacent)",
        "governing_laws": [
            "Companies Act 2017", "Electronic Transactions Ordinance 2002",
            "Prevention of Electronic Crimes Act 2016 (PECA)",
            "Income Tax Ordinance 2001 (IT export income tax exemption)",
            "Sales Tax on Services (provincial)",
        ],
        "compliance_checklist": [
            "PSEB registration enables export tax exemption and government tenders",
            "IT export income tax exemption through June 2026 (FY26)",
            "PECA 2016 cybercrime compliance and incident reporting",
            "Provincial sales-tax-on-services registration (SRB/KPRA/BRA/PRA)",
            "Export repatriation: 35% of proceeds within 180 days",
            "IP ownership agreements with employees; software licensing",
            "EOBI and ESSI for permanent employees",
        ],
        "key_risks": [
            "IT export tax exemption renewal uncertainty beyond FY2026",
            "PKR appreciation reduces PKR value of USD export revenue",
            "Talent attrition (brain drain) raises hiring costs",
            "Client concentration: 2–3 major clients drive bulk of revenue",
        ],
        "financial_signals_to_assess": [
            "Revenue per employee: above USD 15,000/year indicates productivity",
            "Employee cost as % of revenue: typically 50–65%",
            "Export revenue %: benefits from PKR weakness",
            "Revenue growth: listed IT firms 20–40% CAGR recently",
        ],
    },

    "telecom": {
        "regulator": "Pakistan Telecommunication Authority (PTA)",
        "governing_laws": [
            "Pakistan Telecommunication (Re-organisation) Act 1996",
            "Frequency Allocation Board (FAB) regulations",
            "Prevention of Electronic Crimes Act 2016 (PECA)",
            "Competition Act 2010",
        ],
        "compliance_checklist": [
            "Telecom license (PTA Class/Individual) per service type",
            "Spectrum allocation via FAB; annual spectrum fee",
            "Mandatory biometric SIM verification",
            "Universal Service Fund (USF): ~1.5% of revenue",
            "Quality of Service standards; quarterly reporting",
            "Lawful intercept capability per PECA",
            "IMEI registration; non-compliant devices blocked",
        ],
        "key_risks": [
            "5G spectrum auction costs: capital intensive",
            "Subscriber saturation (>180M subscribers)",
            "ARPU compression from price competition",
            "Government mobile/SIM taxes dampen subscriber growth",
            "FX exposure on USD equipment vs PKR revenue",
        ],
        "financial_signals_to_assess": [
            "ARPU: PKR 150–250/month; trend is key",
            "EBITDA margin: 35–45% at mature scale",
            "Capex as % of revenue: 15–25%",
            "Data revenue as % of total (growing)",
        ],
    },

    "automobile": {
        "regulator": "Engineering Development Board (EDB) + FBR + SECP",
        "governing_laws": [
            "Auto Industry Development and Export Policy (AIDEP) 2021-26",
            "Customs Act 1969", "Sales Tax Act 1990",
            "Motor Vehicles Ordinance 1965", "Companies Act 2017",
        ],
        "compliance_checklist": [
            "EDB greenfield/brownfield approval for assembly operations",
            "Minimum local-content thresholds per vehicle category",
            "Type approval by PSQCA",
            "CBU vs CKD customs-duty differential; CKD imports for assembly",
            "Federal Excise Duty (FED) on vehicle sales by engine capacity",
            "SECP oversight on booking/balloting schemes",
            "Emission standards (Euro-II toward Euro-IV)",
        ],
        "key_risks": [
            "PKR depreciation raises JPY/USD-priced CKD kit costs",
            "Demand cyclicality with interest rates and consumer confidence",
            "SRO-based import restrictions affect market dynamics",
            "New-entrant competition (Chinese OEMs)",
            "Budget-to-budget FED changes create demand volatility",
        ],
        "financial_signals_to_assess": [
            "Units sold per quarter vs sector trend",
            "Average selling price (mix shift)",
            "Gross margin: typically 5–12% (thin)",
            "Booking-to-delivery backlog",
        ],
    },

    "oil_marketing": {
        "regulator": "OGRA + Ministry of Energy (Petroleum Division) + FBR",
        "governing_laws": [
            "OGRA Ordinance 2002",
            "Motor Spirit (Petroleum Products) (Distribution and Pricing) Rules 1987",
            "Customs Act 1969", "Companies Act 2017",
        ],
        "compliance_checklist": [
            "OMC license issued by OGRA",
            "Retail prices set by OGRA fortnightly formula; no deviation",
            "Inland Freight Equalization Margin (IFEM) mechanism",
            "Petroleum levy collected and remitted to FBR",
            "PSQCA fuel-specification standards",
            "Underground storage tank environmental/safety standards",
            "Monthly inventory position reported to Ministry of Energy",
        ],
        "key_risks": [
            "OGRA-regulated margins leave little pricing discretion",
            "Circular debt: receivables on subsidised fuel",
            "Inventory losses from price reductions between purchase and sale",
            "Import-parity/FX exposure on USD-priced imports",
        ],
        "financial_signals_to_assess": [
            "Volume market share",
            "Gross margin per litre (tightly regulated)",
            "Inventory turnover (days): fast is preferable",
            "Receivables from government (subsidy delays)",
        ],
    },

    "real_estate": {
        "regulator": "SECP + provincial development authorities (LDA/KDA/RDA/CDA)",
        "governing_laws": [
            "Companies Act 2017",
            "Provincial Real Estate Regulatory Authority Acts",
            "Stamp Duty Acts (provincial)",
            "Income Tax Ordinance 2001 (Section 7E deemed income)",
            "Anti-Money Laundering Act 2010 (FATF high-risk sector)",
        ],
        "compliance_checklist": [
            "Development authority NOC (LDA/KDA/RDA/CDA) before marketing/construction",
            "Building plan approval with structural-engineer certification",
            "SECP registration for real-estate companies/schemes",
            "Enhanced AML/KYC due diligence on buyers (FATF high-risk)",
            "Provincial stamp duty on transfer; CGT on sale",
            "Section 7E deemed-rental income tax on investment properties",
            "Beneficial-ownership disclosure per SECP 2026 amendments",
            "EIA for large residential projects",
        ],
        "key_risks": [
            "FATF scrutiny: real estate is a common money-laundering vehicle",
            "Section 7E enforcement on deemed rental income",
            "Approval delays from development authorities stall projects",
            "Interest-rate sensitivity of property demand",
            "Title/land-record disputes",
        ],
        "financial_signals_to_assess": [
            "Advances from customers vs delivery progress",
            "Inventory (developed/under-development) turnover",
            "Debt/equity: project financing exposure",
            "Receivables recovery on instalment sales",
        ],
    },

    "insurance": {
        "regulator": "SECP (Insurance Division)",
        "governing_laws": [
            "Insurance Ordinance 2000",
            "Insurance Rules 2017 / SEC (Insurance) Rules 2002",
            "Takaful Rules 2012",
            "Companies Act 2017", "Anti-Money Laundering Act 2010",
        ],
        "compliance_checklist": [
            "SECP registration/licensing for insurer or takaful operator",
            "Solvency margin maintained per Insurance Ordinance 2000",
            "Minimum paid-up capital thresholds (life vs non-life)",
            "Actuarial valuation of liabilities (life) filed with SECP",
            "Reinsurance arrangements with approved reinsurers",
            "AML/CFT compliance on policyholder onboarding",
            "Statement of solvency and financial condition report",
        ],
        "key_risks": [
            "Underwriting losses / adverse claims experience",
            "Investment-portfolio risk (equity/bond exposure)",
            "Solvency-margin breach → SECP restrictions",
            "Catastrophe exposure (floods) for non-life",
        ],
        "financial_signals_to_assess": [
            "Combined ratio (non-life): below 100% = underwriting profit",
            "Solvency ratio vs regulatory minimum",
            "Investment yield on the float",
            "Claims ratio trend",
        ],
    },

    "sugar": {
        "regulator": "Provincial cane commissioners + Competition Commission + SECP",
        "governing_laws": [
            "Sugar Factories Control Act (provincial)",
            "Competition Act 2010",
            "Companies Act 2017",
            "Environmental Protection Act 1997",
        ],
        "compliance_checklist": [
            "Crushing licence and season dates set by provincial cane commissioner",
            "Government-notified minimum cane support price",
            "CCP scrutiny on price/production coordination",
            "Export permission (subject to surplus determination)",
            "Effluent/molasses discharge per NEQS",
            "EOBI and ESSI contributions",
        ],
        "key_risks": [
            "Cane support-price increases squeeze margins",
            "Export-permission timing affects inventory clearing",
            "Cartel/price-coordination investigations (CCP)",
            "Crop-yield and monsoon dependence",
        ],
        "financial_signals_to_assess": [
            "Sugar recovery rate (%) from cane",
            "Cane cost as % of revenue",
            "Inventory buildup (unsold sugar) vs export permission",
            "By-product (molasses/ethanol) revenue contribution",
        ],
    },
}


# ── Sector alias matching — maps PSX sector_name values AND keyword_engine
# industry keys onto SECTOR_COMPLIANCE keys (case-insensitive substring). ─────────
SECTOR_ALIASES = {
    # banking / financial
    "commercial banks": "banking", "bank": "banking", "banking": "banking",
    "modarabas": "banking", "leasing": "banking", "inv. banks": "banking",
    "investment banks": "banking", "microfinance": "banking",
    # pharma
    "pharmaceuticals": "pharmaceuticals", "pharma": "pharmaceuticals",
    # cement
    "cement": "cement",
    # oil & gas E&P
    "oil & gas exploration": "oil_gas", "oil and gas exploration": "oil_gas",
    "oil_gas": "oil_gas", "exploration": "oil_gas",
    # oil marketing / refinery
    "oil & gas marketing": "oil_marketing", "oil and gas marketing": "oil_marketing",
    "refinery": "oil_marketing", "oil_marketing": "oil_marketing",
    # fertilizer
    "fertilizer": "fertilizer", "fertiliser": "fertilizer",
    # power / energy
    "power generation": "power", "power": "power", "energy": "power",
    # textile family
    "textile": "textile", "apparel": "textile", "synthetic & rayon": "textile",
    "synthetic": "textile", "woollen": "textile", "jute": "textile",
    "leather": "textile", "spinning": "textile", "weaving": "textile",
    # technology / telecom
    "technology & communication": "technology", "technology": "technology",
    "tech": "technology", "software": "technology",
    "telecom": "telecom", "telecommunication": "telecom",
    # automobile
    "automobile assembler": "automobile", "automobile parts": "automobile",
    "automobile": "automobile", "auto": "automobile",
    # real estate
    "property": "real_estate", "real estate": "real_estate",
    "real estate investment trust": "real_estate", "reit": "real_estate",
    "real_estate": "real_estate",
    # insurance
    "insurance": "insurance",
    # sugar
    "sugar & allied": "sugar", "sugar": "sugar",
}


# ── Resolution ────────────────────────────────────────────────────────────────
def _resolve_sector(sector: str) -> Optional[str]:
    """Map a raw sector string (PSX name or industry key) to a SECTOR_COMPLIANCE key."""
    s = (sector or "").strip().lower()
    if not s:
        return None
    if s in SECTOR_COMPLIANCE:
        return s
    # exact alias
    if s in SECTOR_ALIASES:
        return SECTOR_ALIASES[s]
    # substring alias (handles "TEXTILE COMPOSITE", "OIL & GAS EXPLORATION COMPANIES")
    for alias, key in SECTOR_ALIASES.items():
        if alias in s:
            return key
    return None


def get_compliance_profile(
    sector: str,
    is_listed: bool = True,
    company_type: str = "private_limited",
    # company_type: "private_limited" | "public_unlisted" | "single_member" | "listed_public"
) -> dict:
    """
    Return a compliance profile for a sector. Always returns a usable dict.
    Sector-specific rules apply regardless of listing status — the same
    regulators govern banking/pharma/cement whether listed or not — but the
    UNIVERSAL block switches between listed (SECP + PSX) and unlisted (SECP only).
    """
    key = _resolve_sector(sector)
    sec = SECTOR_COMPLIANCE.get(key, {}) if key else {}
    universal = UNIVERSAL_LISTED_OBLIGATIONS if is_listed else UNIVERSAL_UNLISTED_OBLIGATIONS
    return {
        "matched_sector": key,
        "is_listed": is_listed,
        "company_type": company_type,
        "regulator": sec.get("regulator") or universal["regulator"],
        "governing_laws": (sec.get("governing_laws") or []) + universal["governing_laws"],
        "sector_specific": sec,
        "universal": universal,
    }


def get_compliance_checklist_text(
    sector: str,
    company_name: str = "",
    is_listed: bool = True,
    company_type: str = "private_limited",
) -> str:
    """
    Render a full, deterministic regulatory compliance section (markdown).
    Sector known → sector profile; unknown → universal obligations for the correct
    listing status. Never returns placeholder text; no AI, no uploaded documents.
    """
    from datetime import date
    key = _resolve_sector(sector)
    status = "PSX Listed" if is_listed else "Private / Unlisted"
    name = company_name or "This company"

    lines = [f"## Regulatory Assessment: {name} [{status}]", ""]
    lines.append(f"Source: Static compliance rules (SECP/SBP/DRAP/NEPRA/OGRA) · No AI required · {date.today():%B %Y}")
    lines.append("")
    if not is_listed:
        lines += [
            "> Note: Unlisted company - PSX disclosure obligations do not apply. "
            "SECP private company regulations govern corporate compliance.",
            "",
        ]

    universal = UNIVERSAL_LISTED_OBLIGATIONS if is_listed else UNIVERSAL_UNLISTED_OBLIGATIONS

    if key:
        sec = SECTOR_COMPLIANCE[key]
        lines += [f"**Primary Regulator:** {sec['regulator']}", ""]
        lines += ["### Governing Laws"]
        lines += [f"- {law}" for law in sec.get("governing_laws", [])[:8]]
        lines += ["", "### Compliance Checklist"]
        lines += [f"- {c}" for c in sec.get("compliance_checklist", [])]
        lines += ["", "### Key Regulatory Risks"]
        lines += [f"- {r}" for r in sec.get("key_risks", [])]
        # Corporate-compliance layer (listing-status specific)
        lines += ["", f"### Corporate Obligations ({status})"]
        lines += [f"- {g}" for g in universal["corporate_governance"][:6]]
    else:
        lines += [f"**Primary Regulator:** {universal['regulator']}",
                  "_Sector not matched to a specialised profile - universal obligations apply._", ""]
        lines += ["### Governing Laws"]
        lines += [f"- {law}" for law in universal["governing_laws"]]
        lines += ["", "### Mandatory Filings"]
        lines += [f"- {f}" for f in universal["mandatory_filings"][:6]]
        lines += ["", "### Corporate Governance"]
        lines += [f"- {g}" for g in universal["corporate_governance"][:6]]
        lines += ["", "### Key Regulatory Risks"]
        lines += [f"- {r}" for r in universal.get("key_risks", [])]

    return "\n".join(lines)


def get_financial_signals_context(sector: str) -> list[str]:
    """Return the sector's financial-signal checkpoints (bullet strings), or []."""
    key = _resolve_sector(sector)
    if not key:
        return []
    return list(SECTOR_COMPLIANCE[key].get("financial_signals_to_assess", []))


# ── PSX-announcement red-flag scan (keyword only, no AI) ──────────────────────
def check_compliance_signals_from_announcements(
    announcements: list[str],
    sector: str = "",
) -> list[dict]:
    """
    Scan PSX announcements for compliance red flags.
    Returns list of {signal, severity, text}. No AI. Pure keyword matching.
    """
    RED_FLAG_TERMS = [
        "show cause", "secp notice", "court order", "penalty",
        "fine imposed", "license suspended", "drap action",
        "sbp directive", "nepra penalty", "investigation",
        "winding up", "default", "non-compliance",
        "regulatory action", "fbr notice", "tax evasion",
    ]
    AMBER_TERMS = [
        "legal proceedings", "arbitration", "dispute",
        "contingent liability", "legal claim", "court case",
        "appeal filed", "regulatory review",
    ]
    signals: list[dict] = []
    for ann in announcements or []:
        if not ann:
            continue
        ann_lower = ann.lower()
        for term in RED_FLAG_TERMS:
            if term in ann_lower:
                signals.append({"signal": term, "severity": "high", "text": ann[:200]})
        for term in AMBER_TERMS:
            if term in ann_lower:
                signals.append({"signal": term, "severity": "medium", "text": ann[:200]})
    return signals


def check_compliance_signals_from_documents(
    document_text: str,
    sector: str = "",
    is_listed: bool = True,
) -> list[dict]:
    """
    Scan uploaded document text for compliance signals (listed AND unlisted).
    No AI. Pure keyword matching. Auditor-opinion flags are marked 'critical'.
    Returns a deduplicated list of {signal, severity, source, context}, sorted
    critical → high → medium.
    """
    RED_FLAG_TERMS = [
        "show cause", "secp notice", "court order", "penalty imposed",
        "fine imposed", "license suspended", "drap action",
        "sbp directive", "nepra penalty", "under investigation",
        "winding up petition", "loan default", "non-compliance",
        "regulatory action", "fbr notice", "tax evasion",
        "going concern", "material uncertainty", "qualified opinion",
        "adverse opinion", "disclaimer of opinion",
        "significant doubt", "unable to continue",
        "contingent liability", "pending litigation",
    ]
    AMBER_TERMS = [
        "legal proceedings", "arbitration", "dispute",
        "contingent liability", "legal claim", "court case",
        "appeal filed", "regulatory review", "tax demand",
        "provision for losses", "impairment", "write-off",
        "overdraft", "covenant breach", "waiver obtained",
        "restructured", "rescheduled",
    ]
    AUDIT_RED_FLAGS = [
        "qualified opinion", "adverse opinion",
        "disclaimer of opinion", "going concern",
        "material misstatement", "significant doubt",
        "unable to obtain sufficient",
    ]

    signals: list[dict] = []
    text_lower = document_text.lower() if document_text else ""

    for term in RED_FLAG_TERMS:
        if term in text_lower:
            idx = text_lower.find(term)
            context = document_text[max(0, idx - 100): idx + 100 + len(term)]
            signals.append({"signal": term, "severity": "high",
                            "source": "uploaded_document", "context": context.strip()})

    for term in AMBER_TERMS:
        if term in text_lower:
            idx = text_lower.find(term)
            context = document_text[max(0, idx - 100): idx + 100 + len(term)]
            signals.append({"signal": term, "severity": "medium",
                            "source": "uploaded_document", "context": context.strip()})

    for term in AUDIT_RED_FLAGS:
        if term in text_lower:
            idx = text_lower.find(term)
            context = document_text[max(0, idx - 80): idx + 80 + len(term)]
            signals.append({"signal": f"AUDITOR FLAG: {term}", "severity": "critical",
                            "source": "auditor_opinion", "context": context.strip()})

    seen: set = set()
    unique: list[dict] = []
    for s in signals:
        if s["signal"] not in seen:
            seen.add(s["signal"])
            unique.append(s)

    return sorted(unique, key=lambda x: {"critical": 0, "high": 1, "medium": 2}.get(x["severity"], 3))
