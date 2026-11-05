"""到期提醒和月度汇总。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from .models import Cycle, Status, Subscription

CURRENCY_SYMBOL = {"USD": "$", "CNY": "¥", "HKD": "HK$", "TWD": "NT$", "EUR": "€", "GBP": "£"}


def fmt_money(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return "金额未知"
    sym = CURRENCY_SYMBOL.get(currency or "", "")
    text = f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{sym}{text}" if sym else f"{text} {currency or ''}".strip()


def _days_label(days: int) -> str:
    if days == 0:
        return "今天"
    if days == 1:
        return "明天"
    if days > 0:
        return f"{days} 天后"
    return f"已过 {-days} 天"


@dataclass
class DueItem:
    sub: Subscription
    days: int

    def render(self) -> str:
        s = self.sub
        bits = [f"{s.name}", f"{s.due.strftime('%m-%d')}（{_days_label(self.days)}）"]
        if s.status == Status.TRIAL:
            bits.append("试用结束")
        elif s.amount:
            bits.append(fmt_money(s.amount, s.currency))
        bits.append("自动续费" if s.auto_renew else "需手动处理")
        return "· " + " · ".join(bits)


def due_items(subs: list[Subscription], today: date, window_days: int) -> tuple[list[DueItem], list[DueItem]]:
    """返回（即将到期，已逾期）。已逾期只包含不会自动续费的条目。"""
    upcoming, overdue = [], []
    for s in subs:
        if not s.due or s.status not in (Status.ACTIVE, Status.TRIAL):
            continue
        days = (s.due - today).days
        if 0 <= days <= window_days:
            upcoming.append(DueItem(s, days))
        elif days < 0 and not (s.auto_renew and s.cycle and s.cycle.months):
            overdue.append(DueItem(s, days))
    upcoming.sort(key=lambda d: d.days)
    overdue.sort(key=lambda d: d.days)
    return upcoming, overdue


def render_reminder(today: date, upcoming: list[DueItem], overdue: list[DueItem]) -> str | None:
    if not upcoming and not overdue:
        return None
    lines = [f"📅 订阅提醒 {today.isoformat()}"]
    if overdue:
        lines.append("⚠️ 已过期 / 待确认（处理完记得在 Notion 里改日期）：")
        lines += [d.render() for d in overdue]
    if upcoming:
        lines.append("⏰ 即将到期：")
        lines += [d.render() for d in upcoming]
    return "\n".join(lines)


# ---------- 月度汇总 ----------


def monthly_equivalent(sub: Subscription) -> float | None:
    """折算成每月花费；按量 / 一次性 / 金额未知返回 None。"""
    if sub.amount is None or not sub.cycle or not sub.cycle.months:
        return None
    return sub.amount / sub.cycle.months


def convert(amount: float, frm: str | None, to: str, rates: dict[str, float]) -> float | None:
    """rates 是以某个基准货币为 1 的汇率表（如 USD=1, CNY=7.1）。"""
    frm = (frm or to).upper()
    if frm == to:
        return amount
    if frm not in rates or to not in rates:
        return None
    return amount / rates[frm] * rates[to]


def render_report(subs: list[Subscription], today: date, base: str, rates: dict[str, float]) -> str:
    live = [s for s in subs if s.status in (Status.ACTIVE, Status.TRIAL)]
    by_cat: dict[str, float] = defaultdict(float)
    unknown: list[str] = []
    total = 0.0
    for s in live:
        m = monthly_equivalent(s)
        if m is None:
            continue
        v = convert(m, s.currency, base, rates)
        if v is None:
            unknown.append(s.name)
            continue
        by_cat[s.category or "未分类"] += v
        total += v

    sym = CURRENCY_SYMBOL.get(base, base + " ")
    lines = [
        f"📊 订阅月报 {today.strftime('%Y-%m')}",
        f"在用 {len([s for s in live if s.status == Status.ACTIVE])} 项，试用 {len([s for s in live if s.status == Status.TRIAL])} 项",
        f"固定支出约 {sym}{total:.0f} / 月（约 {sym}{total * 12:.0f} / 年）",
    ]
    if by_cat:
        lines.append("按分类：" + "，".join(f"{k} {sym}{v:.0f}" for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])))
    usage = [s.name for s in live if s.cycle in (Cycle.USAGE,)]
    if usage:
        lines.append("按量付费（未计入）：" + "、".join(usage))
    if unknown:
        lines.append("无法换算币种（未计入）：" + "、".join(unknown))
    upcoming, _ = due_items(subs, today, 30)
    if upcoming:
        lines.append("未来 30 天到期：")
        lines += [d.render() for d in upcoming]
    return "\n".join(lines)
