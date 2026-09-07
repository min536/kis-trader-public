"""Autotuner Slack notification (roadmap item 3).

Delivers the propose-cycle summary (and approval alerts) to the operator Slack
channel via the existing ``SlackNotifier`` (event type ``autotuner_proposal`` ->
operator channel). The notifier is injected, and ``SlackNotifier`` is itself gated
by ``SLACK_ALERTS_ENABLED`` (default off) and ``dry_run`` — so nothing is sent
unless an operator has explicitly configured and enabled Slack. Pure message
formatting is separated from delivery so it is trivially testable.
"""

from __future__ import annotations

from app.notifications.slack import AUTOTUNER_EVENT_TYPE


def notify_propose_cycle(result, *, notifier):
    """Send the propose-cycle summary to Slack via the injected notifier.

    ``notifier`` must expose ``send(event_type, message, *, details=None)`` (the
    project ``SlackNotifier``). Delivery is gated inside the notifier; this just
    formats and hands off.
    """
    message, details = build_propose_cycle_message(result)
    return notifier.send(AUTOTUNER_EVENT_TYPE, message, details=details)


def build_propose_cycle_message(result):
    """Summarise a propose-cycle result into (message, details) for Slack."""
    outcomes = (result or {}).get("outcomes") or []
    built = [o for o in outcomes if o.get("status") == "built"]
    refused = [o for o in outcomes if o.get("status") == "refused"]
    message = (
        f"Autotuner: {len(built)} draft(s) built, {len(refused)} refused — "
        "awaiting human approval."
    )
    details = {
        "built": len(built),
        "refused": len(refused),
        "built_ids": [o.get("proposal_id") for o in built],
        "refused_ids": [o.get("proposal_id") for o in refused],
    }
    return message, details
