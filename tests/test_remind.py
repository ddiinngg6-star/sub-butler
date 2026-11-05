from datetime import date

from sub_butler.models import Cycle, Status, Subscription
from sub_butler.remind import convert, due_items, fmt_money, monthly_equivalent, render_reminder, render_report

TODAY = date(2026, 11, 4)
RATES = {"USD": 1.0, "CNY": 7.0, "HKD": 7.8}


def _subs():
    return [
        Subscription(name="AI Pro", category="AI", amount=20, currency="USD", cycle=Cycle.MONTHLY,
                     due=date(2026, 11, 5), status=Status.ACTIVE, auto_renew=True),
        Subscription(name="试用工具", category="AI", due=date(2026, 11, 7), status=Status.TRIAL),
        Subscription(name="保号卡", category="通讯", amount=1, currency="USD", cycle=Cycle.MONTHLY,
                     due=date(2026, 11, 1), status=Status.ACTIVE, auto_renew=False),
        Subscription(name="年费会员", category="购物", amount=84, currency="CNY", cycle=Cycle.YEARLY,
                     due=date(2027, 11, 7), status=Status.ACTIVE),
        Subscription(name="已取消的", amount=10, currency="USD", cycle=Cycle.MONTHLY,
                     due=date(2026, 11, 5), status=Status.CANCELLED),
        Subscription(name="API", cycle=Cycle.USAGE, status=Status.ACTIVE),
    ]


def test_due_items():
    upcoming, overdue = due_items(_subs(), TODAY, 3)
    assert [d.sub.name for d in upcoming] == ["AI Pro", "试用工具"]
    assert [d.sub.name for d in overdue] == ["保号卡"]
    assert upcoming[0].days == 1


def test_render_reminder():
    upcoming, overdue = due_items(_subs(), TODAY, 3)
    text = render_reminder(TODAY, upcoming, overdue)
    assert "保号卡" in text and "已过 3 天" in text
    assert "AI Pro · 11-05（明天） · $20 · 自动续费" in text
    assert "试用结束" in text
    assert render_reminder(TODAY, [], []) is None


def test_money_and_conversion():
    assert fmt_money(4.99, "USD") == "$4.99"
    assert fmt_money(20.0, "CNY") == "¥20"
    assert fmt_money(None, "USD") == "金额未知"
    assert convert(10, "USD", "CNY", RATES) == 70
    assert convert(70, "CNY", "USD", RATES) == 10
    assert convert(10, "XYZ", "CNY", RATES) is None
    assert monthly_equivalent(Subscription(name="y", amount=120, cycle=Cycle.YEARLY)) == 10


def test_render_report():
    text = render_report(_subs(), TODAY, "CNY", RATES)
    # 20 USD + 1 USD = 147 CNY；84 CNY/年 = 7 CNY/月；合计 154
    assert "固定支出约 ¥154 / 月" in text
    assert "AI ¥140" in text and "已取消的" not in text
    assert "按量付费（未计入）：API" in text
