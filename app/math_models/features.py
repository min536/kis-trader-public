from collections import OrderedDict
from typing import Mapping

FEATURE_GROUP_ORDER: tuple[str, ...] = (
    "base_quality_features",
    "cost_features",
    "technical_features",
    "indicator_quality_features",
    "mean_reversion_features",
    "portfolio_risk_features",
    "price_dynamics_features",
    "final_score_components",
)


def _normalize_group(values: Mapping[str, object] | None) -> OrderedDict[str, float | None]:
    normalized: OrderedDict[str, float | None] = OrderedDict()
    for key, value in (values or {}).items():
        if value is None:
            normalized[str(key)] = None
            continue
        try:
            normalized[str(key)] = float(value)
        except (TypeError, ValueError):
            normalized[str(key)] = None
    return normalized


def build_feature_map(
    *,
    base_quality_features: Mapping[str, object] | None,
    cost_features: Mapping[str, object] | None,
    technical_features: Mapping[str, object] | None = None,
    indicator_quality_features: Mapping[str, object] | None = None,
    mean_reversion_features: Mapping[str, object] | None = None,
    portfolio_risk_features: Mapping[str, object] | None = None,
    price_dynamics_features: Mapping[str, object] | None = None,
    final_score_components: Mapping[str, object] | None = None,
) -> OrderedDict[str, OrderedDict[str, float | None]]:
    return OrderedDict(
        [
            ("base_quality_features", _normalize_group(base_quality_features)),
            ("cost_features", _normalize_group(cost_features)),
            ("technical_features", _normalize_group(technical_features)),
            ("indicator_quality_features", _normalize_group(indicator_quality_features)),
            ("mean_reversion_features", _normalize_group(mean_reversion_features)),
            ("portfolio_risk_features", _normalize_group(portfolio_risk_features)),
            ("price_dynamics_features", _normalize_group(price_dynamics_features)),
            ("final_score_components", _normalize_group(final_score_components)),
        ]
    )


def build_feature_vector(
    feature_map: Mapping[str, Mapping[str, float | None]] | None,
) -> OrderedDict[str, float | None]:
    vector: OrderedDict[str, float | None] = OrderedDict()
    for group_name in FEATURE_GROUP_ORDER:
        group = (feature_map or {}).get(group_name) or {}
        for feature_name, value in group.items():
            vector[f"{group_name}.{feature_name}"] = value
    return vector


def summarize_feature_map(
    feature_map: Mapping[str, Mapping[str, float | None]] | None,
) -> dict[str, str]:
    summaries: dict[str, str] = {}
    for group_name in FEATURE_GROUP_ORDER:
        group = (feature_map or {}).get(group_name) or {}
        available_keys = [key for key, value in group.items() if value is not None]
        summaries[group_name] = ", ".join(available_keys[:4]) if available_keys else "summary unavailable"
    return summaries
