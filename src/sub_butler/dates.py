"""计费周期相关的日期计算。"""

from __future__ import annotations

import calendar
from datetime import date

from .models import Cycle


def add_months(d: date, months: int) -> date:
    """加 N 个月；目标月份没有这一天时取当月最后一天（1/31 + 1 月 = 2/28）。"""
    y, m = divmod(d.month - 1 + months, 12)
    year = d.year + y
    month = m + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def next_due(start: date, cycle: Cycle | None) -> date | None:
    """从某次扣款日推算下一次续费日。按量 / 一次性返回 None。"""
    if cycle is None or cycle.months is None:
        return None
    return add_months(start, cycle.months)


def roll_forward(due: date, cycle: Cycle | None, today: date) -> date:
    """把已经过去的续费日按周期往后推，直到 >= today。

    总是从原始日期按 k 个周期计算，避免 1/31 -> 2/28 -> 3/28 这种日期漂移。
    """
    if cycle is None or cycle.months is None or due >= today:
        return due
    k = 1
    while True:
        candidate = add_months(due, cycle.months * k)
        if candidate >= today:
            return candidate
        k += 1
