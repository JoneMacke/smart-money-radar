from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from app.models import SmartMoneyAggregate, SmartMoneyParticipant, WalletActivity


class SmartMoneyAggregator:
    def __init__(self, window_minutes: int = 60, max_events: int = 5000) -> None:
        self.window = timedelta(minutes=window_minutes)
        self.max_events = max_events
        self._events: deque[WalletActivity] = deque(maxlen=max_events)
        self._seen: set[str] = set()

    def add(self, activity: WalletActivity) -> SmartMoneyAggregate | None:
        if not activity.action_token or activity.event_type not in {"BUY", "SELL"}:
            return None
        if activity.tx_hash in self._seen:
            return self.aggregate(activity)
        if len(self._events) == self.max_events:
            self._seen.discard(self._events[0].tx_hash)
        self._seen.add(activity.tx_hash)
        self._events.append(activity)
        self._prune(activity.timestamp)
        return self.aggregate(activity)

    def aggregate(self, activity: WalletActivity) -> SmartMoneyAggregate:
        cutoff = activity.timestamp - self.window
        participants: dict[str, SmartMoneyParticipant] = {}
        for event in self._events:
            if event.timestamp < cutoff or event.action_token != activity.action_token or event.event_type != activity.event_type:
                continue
            value = event.market.trade_value_usd if event.market and event.market.trade_value_usd else 0.0
            participants[event.wallet.lower()] = SmartMoneyParticipant(
                wallet_label=event.wallet_label,
                wallet_address=event.wallet,
                event_type=event.event_type,
                trade_value_usd=value,
            )
        rows = list(participants.values())
        return SmartMoneyAggregate(
            token=activity.action_token,
            event_type=activity.event_type,
            participants=rows,
            total_value_usd=sum(item.trade_value_usd for item in rows),
            window_minutes=int(self.window.total_seconds() // 60),
        )

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.window
        while self._events and self._events[0].timestamp < cutoff:
            old = self._events.popleft()
            self._seen.discard(old.tx_hash)
