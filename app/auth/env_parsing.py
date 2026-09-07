"""Pure environment-value parsing helpers.

String-to-value parsers shared by the settings loader. These are pure
functions (no os.environ access, no KIS_ENV / credential-scope logic).
Bodies moved verbatim from app/auth/settings.py (R2 settings slimming).
"""

from __future__ import annotations


def strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"환경변수 {name} 는 true/false 형식이어야 합니다.")


def parse_float(name: str, value: str) -> float:
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(f"환경변수 {name} 는 숫자여야 합니다.") from exc


def parse_int(name: str, value: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"환경변수 {name} 는 정수여야 합니다.") from exc


def split_symbol_items(value: str) -> tuple[str, ...]:
    normalized = value.replace("\r", ",").replace("\n", ",")
    return tuple(item.strip() for item in normalized.split(","))


def parse_symbol_list(name: str, value: str) -> tuple[str, ...]:
    symbols: list[str] = []
    seen: set[str] = set()

    for symbol in split_symbol_items(value):
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)

    if not symbols:
        raise ValueError(f"환경변수 {name} 에 최소 1개 종목코드가 필요합니다.")

    return tuple(symbols)


def is_valid_hhmm_window(value: str) -> bool:
    text = str(value or "").strip()
    if "-" not in text:
        return False
    start_text, end_text = (part.strip() for part in text.split("-", 1))
    for part in (start_text, end_text):
        try:
            hour_text, minute_text = part.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except (ValueError, TypeError):
            return False
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return False
    return True
