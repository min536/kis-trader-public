from app.notifications import slack


def test_market_data_quality_routes_to_bottlenecks_with_operator_fallback():
    options = slack.resolve_channel_env_vars("market_data_quality")
    assert slack.BOTTLENECKS_CHANNEL_ENV in options
    assert slack.OPERATOR_CHANNEL_ENV in options
