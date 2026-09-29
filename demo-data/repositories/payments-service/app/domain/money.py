"""Integer minor-unit money helpers (ADR-007)."""

ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW", "VND"})
THREE_DECIMAL_CURRENCIES = frozenset({"KWD", "BHD", "OMR"})


def round_half_even_div(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    twice = remainder * 2
    if twice > denominator or (twice == denominator and quotient % 2 == 1):
        quotient += 1
    return quotient


def apply_bps(amount_cents: int, bps: int) -> int:
    """Return amount * bps / 10_000, rounded half-even. 1 bps = 0.01%."""
    return round_half_even_div(amount_cents * bps, 10_000)


def exponent(currency: str) -> int:
    if currency in ZERO_DECIMAL_CURRENCIES:
        return 0
    if currency in THREE_DECIMAL_CURRENCIES:
        return 3
    return 2
