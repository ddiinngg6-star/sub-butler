from datetime import date

from sub_butler.dates import add_months, next_due, roll_forward
from sub_butler.models import Cycle


def test_add_months_clamps_end_of_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 11, 3), 2) == date(2027, 1, 3)


def test_next_due_by_cycle():
    d = date(2026, 11, 3)
    assert next_due(d, Cycle.MONTHLY) == date(2026, 12, 3)
    assert next_due(d, Cycle.YEARLY) == date(2027, 11, 3)
    assert next_due(d, Cycle.BIMONTHLY) == date(2027, 1, 3)
    assert next_due(d, Cycle.USAGE) is None
    assert next_due(d, None) is None


def test_roll_forward_no_drift():
    # 1/31 每月续费，滚到 4 月应是 4/30，而不是 2/28 -> 3/28 -> 4/28
    assert roll_forward(date(2026, 1, 31), Cycle.MONTHLY, date(2026, 4, 2)) == date(2026, 4, 30)


def test_roll_forward_keeps_future_dates():
    assert roll_forward(date(2026, 12, 1), Cycle.MONTHLY, date(2026, 11, 4)) == date(2026, 12, 1)
    assert roll_forward(date(2026, 11, 1), Cycle.MONTHLY, date(2026, 11, 4)) == date(2026, 12, 1)
    assert roll_forward(date(2026, 11, 1), Cycle.USAGE, date(2026, 11, 4)) == date(2026, 11, 1)
