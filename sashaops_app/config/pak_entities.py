"""
config/pak_entities.py — Curated PSX ticker → alias dictionary.

Supplements the entity dictionary that intelligence/news_relevance.py builds
dynamically from psx_companies. The DB's company_name often misses the *common*
market aliases a headline actually uses (e.g. "OGDCL", "HUBCO", "KE", "GSK"),
so this hand-curated overlay improves entity matching for the most-traded names.

Plain data only — no imports, no I/O.
"""

from __future__ import annotations

PAK_TICKERS: dict[str, list[str]] = {
    # Banks
    "HBL":   ["Habib Bank", "Habib Bank Limited"],
    "UBL":   ["United Bank", "United Bank Limited"],
    "MCB":   ["MCB Bank", "Muslim Commercial Bank"],
    "MEBL":  ["Meezan Bank"],
    "ABL":   ["Allied Bank"],
    "BAHL":  ["Bank AL Habib"],
    "NBP":   ["National Bank", "National Bank of Pakistan"],
    "BAFL":  ["Bank Alfalah"],
    "AKBL":  ["Askari Bank"],
    "BOP":   ["Bank of Punjab"],
    # E&P / Oil & Gas
    "OGDC":  ["OGDCL", "Oil and Gas Development", "Oil & Gas Development Company"],
    "PPL":   ["Pakistan Petroleum"],
    "MARI":  ["Mari Petroleum", "Mari Energies", "Mari Gas"],
    "POL":   ["Pakistan Oilfields"],
    "PSO":   ["Pakistan State Oil"],
    "SHEL":  ["Shell Pakistan"],
    "APL":   ["Attock Petroleum"],
    "SNGP":  ["Sui Northern Gas", "SNGPL"],
    "SSGC":  ["Sui Southern Gas", "SSGCL"],
    # Fertilizer
    "FFC":   ["Fauji Fertilizer", "Fauji Fertilizer Company"],
    "EFERT": ["Engro Fertilizers", "Engro Fertilizer"],
    "ENGRO": ["Engro Corporation", "Engro Corp"],
    "FFBL":  ["Fauji Fertilizer Bin Qasim", "FFBL"],
    "FATIMA":["Fatima Fertilizer"],
    # Cement
    "LUCK":  ["Lucky Cement"],
    "DGKC":  ["DG Khan Cement", "D.G. Khan Cement"],
    "MLCF":  ["Maple Leaf Cement"],
    "CHCC":  ["Cherat Cement"],
    "PIOC":  ["Pioneer Cement"],
    "KOHC":  ["Kohat Cement"],
    "FCCL":  ["Fauji Cement"],
    "ACPL":  ["Attock Cement"],
    "BWCL":  ["Bestway Cement"],
    # Power / IPPs
    "HUBC":  ["Hub Power", "HUBCO"],
    "KAPCO": ["Kot Addu Power", "KAPCO"],
    "KEL":   ["K-Electric", "KE", "Karachi Electric"],
    "NCPL":  ["Nishat Chunian Power"],
    "NPL":   ["Nishat Power"],
    # Pharma
    "GLAXO": ["GlaxoSmithKline Pakistan", "GSK Pakistan", "GSK"],
    "ABOT":  ["Abbott Laboratories Pakistan", "Abbott Pakistan"],
    "AGP":   ["AGP Limited"],
    "FEROZ": ["Ferozsons Laboratories"],
    "HINOON":["Highnoon Laboratories"],
    "HALEON":["Haleon Pakistan"],
    "BFBIO": ["BF Biosciences"],
    "SEARL": ["The Searle Company", "Searle"],
    # Tech / IT
    "SYS":   ["Systems Limited", "Systems Ltd"],
    "TRG":   ["TRG Pakistan"],
    "NETSOL":["NetSol Technologies"],
    "AVN":   ["Avanceon"],
    # Textile
    "ILP":   ["Interloop"],
    "NML":   ["Nishat Mills"],
    "NCL":   ["Nishat Chunian"],
    "GATM":  ["Gul Ahmed Textile"],
    "KTML":  ["Kohinoor Textile"],
    # Autos
    "INDU":  ["Indus Motor", "Toyota Pakistan", "Toyota Indus"],
    "PSMC":  ["Pak Suzuki"],
    "HCAR":  ["Honda Atlas Cars"],
    "MTL":   ["Millat Tractors"],
    # FMCG
    "NESTLE":["Nestle Pakistan"],
    "EFOODS":["Engro Foods"],
    "NATF":  ["National Foods"],
    "UPFL":  ["Unilever Pakistan Foods"],
    # Telecom
    "PTC":   ["Pakistan Telecommunication", "PTCL"],
}


def aliases_for(symbol: str) -> list[str]:
    return PAK_TICKERS.get((symbol or "").strip().upper(), [])
