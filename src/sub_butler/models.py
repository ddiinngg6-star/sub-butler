"""核心数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Cycle(str, Enum):
    MONTHLY = "monthly"
    BIMONTHLY = "bimonthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    USAGE = "usage"
    ONCE = "once"

    @property
    def months(self) -> int | None:
        """周期对应的月数；按量 / 一次性返回 None。"""
        return {
            Cycle.MONTHLY: 1,
            Cycle.BIMONTHLY: 2,
            Cycle.QUARTERLY: 3,
            Cycle.YEARLY: 12,
        }.get(self)


class Status(str, Enum):
    ACTIVE = "active"
    TRIAL = "trial"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class EventType(str, Enum):
    PURCHASE = "purchase"  # 新购买 / 新开通
    RENEWAL = "renewal"  # 续费成功 / 周期扣款收据
    TRIAL_STARTED = "trial_started"  # 开始试用
    RENEWAL_NOTICE = "renewal_notice"  # 即将续费 / 即将到期提醒
    CANCELLED = "cancelled"  # 已取消订阅
    EXPIRED = "expired"  # 已到期 / 服务被暂停
    PAYMENT_FAILED = "payment_failed"  # 扣款失败
    CREDIT_EXHAUSTED = "credit_exhausted"  # 预付额度用完 / 余额不足
    NOT_BILLING = "not_billing"  # 与账单无关（营销、验证码等）


@dataclass
class Subscription:
    """订阅库里的一行。"""

    name: str
    page_id: str | None = None
    provider: str | None = None
    category: str | None = None
    kind: str | None = None
    plan: str | None = None
    amount: float | None = None
    currency: str | None = None
    cycle: Cycle | None = None
    due: date | None = None
    status: Status | None = None
    auto_renew: bool = False
    notes: str | None = None
    # 以下字段只读不写，保留给提醒 / 展示用
    account: str | None = None
    payment_method: str | None = None
    manage_url: str | None = None

    @property
    def is_live(self) -> bool:
        return self.status in (Status.ACTIVE, Status.TRIAL, None)


@dataclass
class MailMessage:
    message_id: str
    sender: str
    subject: str
    date: date | None
    text: str


@dataclass
class BillingEvent:
    """大模型从一封邮件里提取出的账单事件。"""

    event: EventType
    provider: str = ""
    product: str = ""
    plan: str | None = None
    category: str | None = None
    kind: str | None = None
    amount: float | None = None
    currency: str | None = None
    cycle: Cycle | None = None
    event_date: date | None = None
    next_due: date | None = None
    auto_renew: bool | None = None
    summary: str = ""
    source: MailMessage | None = field(default=None, repr=False)
