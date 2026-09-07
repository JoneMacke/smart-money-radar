from __future__ import annotations

from collections.abc import Iterable

from app.models import WalletActivity

DEFAULT_ALERT_EVENT_TYPES = frozenset({"BUY", "SELL", "SWAP"})


def parse_alert_event_types(value: str | Iterable[str] | None) -> frozenset[str]:
    """Normalize comma-separated or iterable alert event names."""
    if value is None:
        return DEFAULT_ALERT_EVENT_TYPES
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value
    parsed = frozenset(str(item).strip().upper() for item in values if str(item).strip())
    return parsed or DEFAULT_ALERT_EVENT_TYPES


def activity_score(activity: WalletActivity) -> float:
    """Return the score that will be shown and used for alert filtering."""
    return activity.signal.score if activity.signal else activity.confidence * 100


def should_send_alert(
    activity: WalletActivity,
    min_score: float = 60.0,
    event_types: str | Iterable[str] | None = None,
) -> bool:
    """Decide whether an activity is actionable enough for Telegram."""
    allowed_events = parse_alert_event_types(event_types)
    return (
        activity.event_type.upper() in allowed_events
        and activity_score(activity) >= max(0.0, float(min_score))
    )
