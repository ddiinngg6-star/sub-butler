"""核心逻辑：把账单事件合并进订阅库。"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .dates import next_due, roll_forward
from .extract import Extractor
from .mail import MailSource
from .models import BillingEvent, EventType, Status, Subscription
from .notion import SubscriptionStore
from .state import State

log = logging.getLogger(__name__)

NOTES_LIMIT = 1500


# ---------- 匹配已有订阅 ----------


def _norm(s: str | None) -> str:
    return re.sub(r"[\s\-_.,·]+", "", (s or "").lower())


def tokens(s: str | None) -> set[str]:
    """英文按单词、中文按双字切分，用于粗略的相似度计算。"""
    s = (s or "").lower()
    out = set(re.findall(r"[a-z0-9]+", s))
    for run in re.findall(r"[一-鿿]+", s):
        if len(run) == 1:
            out.add(run)
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


def _similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_match(event: BillingEvent, subs: list[Subscription]) -> Subscription | None:
    prov = _norm(event.provider)
    if not prov:
        return None
    candidates = [
        s for s in subs if _norm(s.provider) == prov or (prov and prov in _norm(s.name))
    ]
    if not candidates:
        return None

    ev_tokens = tokens(f"{event.product} {event.plan or ''}") - tokens(event.provider)

    def score(s: Subscription) -> float:
        sc = _similarity(ev_tokens, tokens(f"{s.name} {s.plan or ''}") - tokens(s.provider))
        if event.amount is not None and s.amount is not None and abs(event.amount - s.amount) < 0.01:
            sc += 0.3
        if event.category and s.category == event.category:
            sc += 0.2
        if s.is_live:
            sc += 0.1
        return sc

    best = max(candidates, key=score)
    best_score = score(best)
    live = [s for s in candidates if s.is_live]
    # 同一服务商只有一个在用的订阅时，放宽阈值（例如 "Claude Pro" 的续费收据）
    if len(live) == 1 and best is live[0] and best_score >= 0.1:
        return best
    return best if best_score >= 0.35 else None


# ---------- 应用事件 ----------


@dataclass
class Change:
    sub: Subscription
    created: bool
    fields: set[str]
    summary: str


def _append_note(notes: str | None, line: str) -> str:
    text = f"{notes}\n{line}" if notes else line
    return text[-NOTES_LIMIT:]


def _display_name(event: BillingEvent) -> str:
    product = event.product or "订阅"
    if event.provider and _norm(event.provider) not in _norm(product):
        return f"{event.provider} {product}"
    return product


def apply_event(event: BillingEvent, existing: Subscription | None, today: date) -> tuple[Subscription, set[str], list[str]]:
    """返回（更新后的订阅，改动的字段，需要立即提醒的告警）。不会修改传入的 existing。"""
    sub = copy.deepcopy(existing) if existing else Subscription(name=_display_name(event))
    changed: set[str] = set() if existing else {"name"}
    alerts: list[str] = []

    def setf(name: str, value) -> None:
        if value is None:
            return
        if getattr(sub, name) != value:
            setattr(sub, name, value)
            changed.add(name)

    if not existing:
        setf("provider", event.provider or None)
        setf("category", event.category)
        setf("kind", event.kind)
        setf("plan", event.plan)

    ev = event.event
    when = event.event_date or today

    if ev in (EventType.PURCHASE, EventType.RENEWAL):
        setf("status", Status.ACTIVE)
        setf("amount", event.amount)
        setf("currency", event.currency)
        setf("cycle", event.cycle)
        setf("auto_renew", event.auto_renew)
        cycle = event.cycle or sub.cycle
        setf("due", event.next_due or next_due(when, cycle))
    elif ev == EventType.TRIAL_STARTED:
        setf("status", Status.TRIAL)
        setf("amount", event.amount if event.amount is not None else (0.0 if not existing else None))
        setf("currency", event.currency)
        setf("cycle", event.cycle)
        setf("due", event.next_due)
    elif ev == EventType.RENEWAL_NOTICE:
        setf("due", event.next_due)
        setf("amount", event.amount)
        setf("currency", event.currency)
        if event.next_due:
            alerts.append(f"{sub.name} 将在 {event.next_due.isoformat()} 续费/到期")
    elif ev == EventType.CANCELLED:
        setf("status", Status.CANCELLED)
        setf("auto_renew", False)
    elif ev in (EventType.EXPIRED, EventType.CREDIT_EXHAUSTED):
        setf("status", Status.EXPIRED)
        alerts.append(f"{sub.name}：{event.summary or '已到期 / 额度用完'}")
    elif ev == EventType.PAYMENT_FAILED:
        alerts.append(f"{sub.name} 扣款失败：{event.summary or '请检查支付方式'}")

    if event.summary:
        line = f"[{when.isoformat()}] {event.summary}（自动同步）"
        if line not in (sub.notes or ""):
            setf("notes", _append_note(sub.notes, line))
    return sub, changed, alerts


def rollover(subs: list[Subscription], today: date) -> list[tuple[Subscription, date]]:
    """自动续费的订阅，如果续费日已过，按周期往后推。返回 (订阅, 旧日期)。"""
    rolled = []
    for s in subs:
        if not s.auto_renew or not s.due or s.status != Status.ACTIVE:
            continue
        new_due = roll_forward(s.due, s.cycle, today)
        if new_due != s.due:
            rolled.append((s, s.due))
            s.due = new_due
    return rolled


# ---------- 同步流程 ----------


@dataclass
class SyncReport:
    scanned: int = 0
    billing: int = 0
    changes: list[Change] = field(default_factory=list)
    rolled: list[tuple[Subscription, date]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"🔄 订阅同步：检查了 {self.scanned} 封候选邮件，其中 {self.billing} 封是账单。"]
        for c in self.changes:
            verb = "新增" if c.created else "更新"
            lines.append(f"· {verb}「{c.sub.name}」：{c.summary}")
        for s, old in self.rolled:
            lines.append(f"· 自动续费「{s.name}」到期日 {old.isoformat()} → {s.due.isoformat()}")
        for a in self.alerts:
            lines.append(f"⚠️ {a}")
        for e in self.errors:
            lines.append(f"❌ {e}")
        return "\n".join(lines)

    @property
    def has_news(self) -> bool:
        return bool(self.changes or self.rolled or self.alerts or self.errors)


def run_sync(
    *,
    mail: MailSource,
    extractor: Extractor,
    store: SubscriptionStore,
    state: State,
    today: date,
    lookback_days: int = 7,
    since: date | None = None,
    dry_run: bool = False,
) -> SyncReport:
    report = SyncReport()
    subs = store.load()
    context = {
        "providers": sorted({s.provider for s in subs if s.provider} | set(store.options("provider"))),
        "categories": store.options("category"),
        "kinds": store.options("kind"),
    }

    start = since or state.last_sync or (today - timedelta(days=lookback_days))
    messages = mail.fetch(start)
    for msg in messages:
        if state.seen(msg.message_id):
            continue
        report.scanned += 1
        try:
            event = extractor.extract(msg, context)
        except Exception as e:  # 单封失败不影响其他邮件，下次重试
            report.errors.append(f"解析失败「{msg.subject[:40]}」：{e}")
            continue
        if event.event == EventType.NOT_BILLING:
            if not dry_run:
                state.mark(msg.message_id, "not_billing")
            continue

        report.billing += 1
        existing = find_match(event, subs)
        sub, changed, alerts = apply_event(event, existing, today)
        report.alerts.extend(alerts)
        if changed:
            try:
                if not dry_run:
                    if existing:
                        store.update(sub, changed)
                    else:
                        store.create(sub)
            except Exception as e:
                report.errors.append(f"写入 Notion 失败「{sub.name}」：{e}")
                continue
            if existing:
                subs[subs.index(existing)] = sub
            else:
                subs.append(sub)
                if sub.provider and sub.provider not in context["providers"]:
                    context["providers"].append(sub.provider)
            report.changes.append(Change(sub, existing is None, changed, event.summary or event.event.value))
        if not dry_run:
            state.mark(msg.message_id, event.event.value)

    report.rolled = rollover(subs, today)
    if not dry_run:
        for s, _ in report.rolled:
            try:
                store.update(s, {"due"})
            except Exception as e:
                report.errors.append(f"更新续费日失败「{s.name}」：{e}")
        state.last_sync = today
    return report
