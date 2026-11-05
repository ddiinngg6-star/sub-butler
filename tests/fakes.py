"""测试用的假 Notion、假邮箱、假大模型。所有数据都是虚构的。"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sub_butler.config import NotionConfig
from sub_butler.models import BillingEvent, MailMessage

SCHEMA_TYPES = {
    "产品名称": "title",
    "服务商": "select",
    "分类": "select",
    "类型": "select",
    "套餐/等级": "rich_text",
    "费用": "number",
    "币种": "select",
    "计费周期": "select",
    "续费/到期日": "date",
    "状态": "select",
    "自动续费": "checkbox",
    "备注": "rich_text",
    "登录账号": "email",
    "支付方式": "rich_text",
    "管理链接": "url",
}

OPTIONS = {
    "服务商": ["ExampleAI", "CloudCo"],
    "分类": ["AI", "VPS", "通讯"],
    "类型": ["订阅", "API 额度", "免费权益"],
    "币种": ["USD", "CNY"],
    "计费周期": ["月付", "年付", "按量", "一次性", "每两月"],
    "状态": ["使用中", "试用中", "已取消", "已过期"],
}


class FakeNotion:
    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, Any]] = []

    def get_data_source(self, data_source_id: str) -> dict[str, Any]:
        props = {}
        for name, t in SCHEMA_TYPES.items():
            p: dict[str, Any] = {"type": t}
            if t == "select":
                p["select"] = {"options": [{"name": o} for o in OPTIONS.get(name, [])]}
            props[name] = p
        return {"properties": props}

    def query_all(self, data_source_id: str) -> list[dict[str, Any]]:
        return [{"id": pid, "properties": props} for pid, props in self.pages.items()]

    def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        pid = str(uuid.uuid4())
        self.pages[pid] = self._normalize(properties)
        self.calls.append(("create", properties))
        return {"id": pid}

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        self.pages[page_id].update(self._normalize(properties))
        self.calls.append(("update", properties))
        return {"id": page_id}

    @staticmethod
    def _normalize(properties: dict[str, Any]) -> dict[str, Any]:
        """把写入格式转换成读取格式（Notion 读出来的富文本带 plain_text）。"""
        out = {}
        for name, val in properties.items():
            t = SCHEMA_TYPES[name]
            v = val[t]
            if t in ("title", "rich_text"):
                v = [{"plain_text": part["text"]["content"]} for part in v]
            out[name] = {"type": t, t: v}
        return out


def seed(fake: FakeNotion, **values: Any) -> str:
    """直接往假 Notion 里放一行，键用逻辑字段名。"""
    from sub_butler.notion import SubscriptionStore
    from sub_butler.models import Subscription

    store = SubscriptionStore(fake, "ds", NotionConfig())
    sub = store.create(Subscription(**values))
    fake.calls.clear()
    return sub.page_id


class FakeMail:
    def __init__(self, messages: list[MailMessage]):
        self.messages = messages
        self.since: date | None = None

    def fetch(self, since: date) -> list[MailMessage]:
        self.since = since
        return list(self.messages)


class FakeExtractor:
    """按 message_id 返回预设的事件。"""

    def __init__(self, events: dict[str, BillingEvent | Exception]):
        self.events = events
        self.contexts: list[dict[str, list[str]]] = []

    def extract(self, msg: MailMessage, context: dict[str, list[str]]) -> BillingEvent:
        self.contexts.append(context)
        ev = self.events[msg.message_id]
        if isinstance(ev, Exception):
            raise ev
        ev.source = msg
        return ev


def mail(mid: str, subject: str = "Receipt", sender: str = "billing@example.com", d: date | None = None) -> MailMessage:
    return MailMessage(mid, sender, subject, d or date(2026, 11, 3), "body")
