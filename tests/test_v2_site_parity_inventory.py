"""Parity inventory for replacing Streamlit with the v2 site.

The v2 site migration must not silently shrink the old Streamlit dashboard
surface. This test pins every Streamlit page/focus area to a v2 data contract
key before the frontend migration begins.
"""

from app.dashboard import v2_site_payload


EXPECTED_PARITY_SURFACES = {
    "Desk/Triage": "TRIAGE",
    "Desk/Execution": "TRACE_EVENTS",
    "Desk/Capital": "ACCOUNT",
    "Desk/Lab": "LAB",
    "Book": "BOOK",
    "Trace": "TRACE_EVENTS",
    "settings/freshness": "RAW_DIAGNOSTICS",
}


def test_streamlit_surfaces_are_pinned_to_v2_contract_keys() -> None:
    assert v2_site_payload.STREAMLIT_PARITY_SURFACES == EXPECTED_PARITY_SURFACES
    assert set(EXPECTED_PARITY_SURFACES.values()) <= set(
        v2_site_payload.V2_SITE_PARITY_CONTRACT_KEYS
    )


def test_v2_base_contract_keys_are_preserved() -> None:
    assert set(v2_site_payload.V2_SITE_BASE_CONTRACT_KEYS) == {
        "NOW_ISO",
        "TRADING_DATE",
        "ACCOUNT",
        "OTHER_ACCOUNTS",
        "POSITIONS",
        "ORDERS",
        "CYCLES",
        "CYCLE_HIST",
        "ENDPOINTS",
        "BUDGETS",
        "ENGINE",
        "PNL_HIST",
        "INTRADAY",
        "EQUITY_HISTORY",
        "EVENTS",
        "NAV_GROUPS",
    }
