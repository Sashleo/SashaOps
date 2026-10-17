from .sbp_pakistan import SBPPakistanForexScraper

# Pakistan-only: State Bank of Pakistan forex (PKR rates).
ALL_CENTRAL_BANK_SCRAPERS = [
    SBPPakistanForexScraper(),
]
