from datetime import datetime

from app.core.time_utils import KOREA_TZ


def format_krw(value) -> str:
    if value is None:
        return "데이터 없음"
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return "데이터 없음"
    return f"{amount:,}원"


def format_signed_krw(value) -> str:
    if value is None:
        return "데이터 없음"
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return "데이터 없음"
    if amount > 0:
        return f"+{amount:,}원"
    if amount < 0:
        return f"-{abs(amount):,}원"
    return "0원"


def format_pct(value) -> str:
    if value is None:
        return "데이터 부족"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "데이터 부족"
    return f"{number:.2f}%"


def format_signed_pct(value) -> str:
    if value is None:
        return "데이터 부족"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "데이터 부족"
    if number > 0:
        return f"+{number:.2f}%"
    if number < 0:
        return f"-{abs(number):.2f}%"
    return "0.00%"


def format_optional_ratio(value) -> str:
    if value is None:
        return "표본 부족"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "표본 부족"


def format_timestamp(value: str | None) -> str:
    if not value:
        return "데이터 없음"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KOREA_TZ)
    else:
        dt = dt.astimezone(KOREA_TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S")
