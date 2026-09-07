def _require_env(env):
    if env not in ("mock", "live"):
        raise ValueError(f"env must be 'mock' or 'live', got {env!r}")


def resolve_quote_tr_id(env):
    return "HHDFS00000300"


def resolve_balance_tr_id(env):
    return "VTTS3012R" if env == "mock" else "TTTS3012R"


def resolve_psamount_tr_id(env):
    return "VTTS3007R" if env == "mock" else "TTTS3007R"


def resolve_order_tr_id(env, side, ovrs_excg_cd):
    _require_env(env)
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if ovrs_excg_cd not in ("NASD", "NYSE", "AMEX"):
        raise NotImplementedError(
            f"only US exchanges supported in v1, got {ovrs_excg_cd!r}"
        )
    if env == "live":
        raise NotImplementedError(
            "live overseas order tr_ids not enabled in v1; operator gate required"
        )
    return "VTTT1002U" if side == "buy" else "VTTT1006U"


def resolve_price_detail_tr_id(env):
    return "HHDFS76200200"
