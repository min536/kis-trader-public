def format_krw(value: int) -> str:
    return f"{int(value):,}원"


def format_qty(value: int) -> str:
    return f"{int(value):,}주"


def format_signed_krw(value: int) -> str:
    amount = int(value)
    if amount > 0:
        return f"+{amount:,}원"
    if amount < 0:
        return f"-{abs(amount):,}원"
    return "0원"


def format_signed_pct(value: float) -> str:
    number = float(value)
    if number > 0:
        return f"+{number:.2f}%"
    if number < 0:
        return f"-{abs(number):.2f}%"
    return "0.00%"


def format_bps(value: float) -> str:
    return f"{value:.1f}bps"
