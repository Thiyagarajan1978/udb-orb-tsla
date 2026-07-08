"""Alert delivery: Resend email + generic webhook. Fully optional.

Each channel self-disables if its env vars are missing, so the live loop runs fine with
no alerting configured. Message formatting mirrors the Pine v12.3 dynamic alert style
([ENTRY], [EXIT-WIN], [STATE], ...), including symbol, price, and ET time.
"""
from __future__ import annotations

from typing import Iterable

from ..config import get_env

# event type -> ([TAG], human action)
_EVENT_META = {
    "primary_entry":     ("ENTRY", "Enter primary breakout at market"),
    "reversal_entry":    ("ENTRY-REV", "Enter 2x reversal at market"),
    "be_retrace_fired":  ("STATE", "Move stop to ENTRY (trade risk-free)"),
    "tp_full":           ("EXIT-WIN", "Full take-profit close"),
    "partial_exit":      ("EXIT-PARTIAL", "Partial close at TP; remainder trails"),
    "be_trail_exit":     ("EXIT-WIN", "BE-trail close in profit"),
    "be_stop_exit":      ("EXIT-BE", "Breakeven close (saved by BE)"),
    "vwap_cross_exit":   ("EXIT-VWAP", "Trail half closed on VWAP cross"),
    "base_sl_exit":      ("EXIT-SL", "Base stop-loss hit"),
    "eod_exit":          ("EXIT-EOD", "End-of-day forced close"),
}


def format_event(symbol: str, tf_min: int, e) -> str:
    tag, action = _EVENT_META.get(e.type, ("EVENT", ""))
    ts = e.ts
    when = ts.strftime("%Y-%m-%d %H:%M ET") if hasattr(ts, "strftime") else str(ts)
    parts = [
        f"[{tag}] UDB-ORB | {e.type.replace('_', ' ').upper()}",
        f"Action: {action}",
        f"{symbol} {tf_min}m",
        f"Price: ${e.price:.2f}",
        f"Dir: {e.direction}",
    ]
    if e.pnl is not None:
        parts.append(f"P&L: {'+' if e.pnl >= 0 else ''}{e.pnl:.2f}")
    if e.note:
        parts.append(e.note)
    parts.append(f"Time: {when}")
    return " | ".join(parts)


class Notifier:
    def __init__(self, cfg: dict, symbol: str, tf_min: int):
        alerts = cfg.get("alerts", {})
        self.enabled = bool(alerts.get("enabled", True))
        self.channels = set(alerts.get("channels", []))
        self.events = set(alerts.get("events", []))
        self.symbol = symbol
        self.tf_min = tf_min
        self.resend_key = get_env("RESEND_API_KEY")
        self.alert_from = get_env("ALERT_FROM")
        self.alert_to = get_env("ALERT_TO")
        self.webhook = get_env("ALERT_WEBHOOK_URL")

    def _wants(self, event_type: str) -> bool:
        return self.enabled and (not self.events or event_type in self.events)

    def notify(self, event) -> bool:
        """Send one event across configured channels. Returns True if anything was sent."""
        if not self._wants(event.type):
            return False
        msg = format_event(self.symbol, self.tf_min, event)
        subject = f"UDB-ORB {self.symbol}: {event.type.replace('_', ' ')}"
        sent = False
        if "email" in self.channels and self._email(subject, msg):
            sent = True
        if "webhook" in self.channels and self._webhook(event, msg):
            sent = True
        if not self.channels:  # no channel configured -> console fallback
            print(msg)
            sent = True
        return sent

    def _email(self, subject: str, body: str) -> bool:
        if not (self.resend_key and self.alert_from and self.alert_to):
            return False
        try:
            import requests
            r = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.resend_key}", "Content-Type": "application/json"},
                json={"from": self.alert_from, "to": [self.alert_to], "subject": subject,
                      "text": body},
                timeout=20,
            )
            return r.status_code < 300
        except Exception as exc:  # pragma: no cover - network
            print(f"[notifier] email failed: {exc}")
            return False

    def _webhook(self, event, msg: str) -> bool:
        if not self.webhook:
            return False
        try:
            import requests
            payload = {
                "content": msg,  # Discord-compatible
                "text": msg,     # Slack-compatible
                "event": {
                    "type": event.type, "direction": event.direction, "price": event.price,
                    "qty": event.qty, "pnl": event.pnl, "reason": event.reason,
                    "ts": str(event.ts),
                },
            }
            r = requests.post(self.webhook, json=payload, timeout=20)
            return r.status_code < 300
        except Exception as exc:  # pragma: no cover - network
            print(f"[notifier] webhook failed: {exc}")
            return False
