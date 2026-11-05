"""Notion API 客户端，以及订阅库的读写。"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Protocol

import httpx

from .config import NotionConfig
from .models import Cycle, Status, Subscription

log = logging.getLogger(__name__)

NOTION_VERSION = "2025-09-03"
TEXT_LIMIT = 2000  # Notion 单段富文本的长度上限


class NotionError(Exception):
    pass


class NotionAPI(Protocol):
    def get_data_source(self, data_source_id: str) -> dict[str, Any]: ...
    def update_data_source(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]: ...
    def query_all(self, data_source_id: str) -> list[dict[str, Any]]: ...
    def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]: ...
    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]: ...


class NotionClient:
    base_url = "https://api.notion.com/v1"

    def __init__(self, token: str, http: httpx.Client | None = None, max_retries: int = 3):
        if not token:
            raise NotionError("缺少 Notion token（notion.token）")
        self._http = http or httpx.Client(timeout=30)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self._max_retries = max_retries

    def _request(self, method: str, path: str, json: Any = None) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            resp = self._http.request(method, self.base_url + path, headers=self._headers, json=json)
            if resp.status_code == 429 and attempt < self._max_retries:
                time.sleep(float(resp.headers.get("Retry-After", "1")))
                continue
            if resp.status_code >= 400:
                try:
                    msg = resp.json().get("message", resp.text)
                except ValueError:
                    msg = resp.text
                raise NotionError(f"Notion API {method} {path} 失败（{resp.status_code}）：{msg}")
            return resp.json()
        raise NotionError("Notion API 多次限流，稍后再试")

    def resolve_data_source(self, database_id: str) -> str:
        db = self._request("GET", f"/databases/{database_id}")
        sources = db.get("data_sources") or []
        if not sources:
            raise NotionError("这个数据库下没有 data source，请改用 notion.data_source_id")
        return sources[0]["id"]

    def get_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self._request("GET", f"/data_sources/{data_source_id}")

    def update_data_source(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/data_sources/{data_source_id}", json={"properties": properties})

    def query_all(self, data_source_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        body: dict[str, Any] = {"page_size": 100}
        while True:
            page = self._request("POST", f"/data_sources/{data_source_id}/query", json=body)
            results.extend(page.get("results", []))
            if not page.get("has_more"):
                return results
            body["start_cursor"] = page["next_cursor"]

    def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/pages",
            json={
                "parent": {"type": "data_source_id", "data_source_id": data_source_id},
                "properties": properties,
            },
        )

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/pages/{page_id}", json={"properties": properties})


# ---------- 属性编解码 ----------


def _rich(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text[:TEXT_LIMIT]}}] if text else []


def encode_property(ptype: str, value: Any) -> dict[str, Any]:
    """把 Python 值编码成 Notion 属性 JSON。value 为 None 表示清空。"""
    if ptype == "title":
        return {"title": _rich(str(value or ""))}
    if ptype == "rich_text":
        return {"rich_text": _rich(str(value or ""))}
    if ptype == "number":
        return {"number": None if value is None else float(value)}
    if ptype == "select":
        return {"select": None if not value else {"name": str(value)}}
    if ptype == "status":
        return {"status": None if not value else {"name": str(value)}}
    if ptype == "multi_select":
        vals = value if isinstance(value, list) else ([value] if value else [])
        return {"multi_select": [{"name": str(v)} for v in vals]}
    if ptype == "date":
        if value is None:
            return {"date": None}
        start = value.isoformat() if isinstance(value, date) else str(value)
        return {"date": {"start": start}}
    if ptype == "checkbox":
        return {"checkbox": bool(value)}
    if ptype in ("email", "url", "phone_number"):
        return {ptype: value or None}
    raise NotionError(f"不支持写入这种属性类型：{ptype}")


def decode_property(prop: dict[str, Any]) -> Any:
    ptype = prop.get("type")
    v = prop.get(ptype)
    if ptype in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in v or []) or None
    if ptype in ("select", "status"):
        return v.get("name") if v else None
    if ptype == "multi_select":
        return [o.get("name") for o in v or []]
    if ptype == "date":
        if not v or not v.get("start"):
            return None
        return date.fromisoformat(v["start"][:10])
    if ptype in ("number", "checkbox", "email", "url", "phone_number"):
        return v
    return None


# ---------- 订阅库 ----------

# 会被自动写入的字段；账号、支付方式、管理链接等只读不写
WRITABLE =("name", "provider", "category", "kind", "plan", "amount", "currency", "cycle", "due", "status", "auto_renew", "notes")


class SubscriptionStore:
    """在 Notion 数据库和 Subscription 对象之间转换。"""

    def __init__(self, api: NotionAPI, data_source_id: str, cfg: NotionConfig):
        self.api = api
        self.ds_id = data_source_id
        self.fields = cfg.fields
        self.values = cfg.values
        self._schema: dict[str, dict[str, Any]] | None = None

    # 映射：逻辑值 <-> 显示值
    def _to_display(self, kind: str, value: str | None) -> str | None:
        if value is None:
            return None
        return self.values.get(kind, {}).get(value, value)

    def _from_display(self, kind: str, display: str | None) -> str | None:
        if display is None:
            return None
        for logical, shown in self.values.get(kind, {}).items():
            if shown == display:
                return logical
        return None

    @property
    def schema(self) -> dict[str, dict[str, Any]]:
        if self._schema is None:
            ds = self.api.get_data_source(self.ds_id)
            self._schema = ds.get("properties", {})
        return self._schema

    def check_fields(self) -> list[str]:
        """返回配置里写了、但数据库里不存在的字段，便于 `check` 命令提示。"""
        return [f"{k} -> {v}" for k, v in self.fields.items() if k in WRITABLE and v not in self.schema]

    def options(self, logical: str) -> list[str]:
        prop = self.schema.get(self.fields.get(logical, ""), {})
        ptype = prop.get("type")
        if ptype in ("select", "multi_select", "status"):
            return [o["name"] for o in prop.get(ptype, {}).get("options", [])]
        return []

    def ensure_option(self, prop_name: str, value: str) -> None:
        """单选 / 多选里没有这个选项时，先把它加进数据库。

        Notion 拒绝写入不存在的选项（"If a new select option is needed, the data source
        must be updated to add it"），所以新服务商、新币种要先补到选项列表里。
        已有选项按 id 原样保留。
        """
        prop = self.schema.get(prop_name)
        if not prop or prop.get("type") not in ("select", "multi_select"):
            return
        ptype = prop["type"]
        existing = prop.setdefault(ptype, {}).setdefault("options", [])
        if any(o.get("name") == value for o in existing):
            return
        keep = [
            {k: o[k] for k in ("id", "name", "color") if o.get(k)} for o in existing
        ]
        try:
            ds = self.api.update_data_source(
                self.ds_id, {prop_name: {ptype: {"options": keep + [{"name": value}]}}}
            )
        except NotionError as e:
            log.warning("无法为「%s」添加选项「%s」，将直接尝试写入：%s", prop_name, value, e)
            return
        new_schema = (ds or {}).get("properties")
        if new_schema and prop_name in new_schema:
            self._schema = new_schema
        else:
            existing.append({"name": value})

    def load(self) -> list[Subscription]:
        return [self._decode(page) for page in self.api.query_all(self.ds_id)]

    def _decode(self, page: dict[str, Any]) -> Subscription:
        props = page.get("properties", {})

        def get(logical: str) -> Any:
            name = self.fields.get(logical)
            return decode_property(props[name]) if name in props else None

        cycle = self._from_display("cycle", get("cycle"))
        status = self._from_display("status", get("status"))
        return Subscription(
            page_id=page.get("id"),
            name=get("name") or "",
            provider=get("provider"),
            category=get("category"),
            kind=get("kind"),
            plan=get("plan"),
            amount=get("amount"),
            currency=get("currency"),
            cycle=Cycle(cycle) if cycle else None,
            due=get("due"),
            status=Status(status) if status else None,
            auto_renew=bool(get("auto_renew")),
            notes=get("notes"),
            account=get("account"),
            payment_method=get("payment_method"),
            manage_url=get("manage_url"),
        )

    def _encode(self, sub: Subscription, only: set[str] | None = None) -> dict[str, Any]:
        values: dict[str, Any] = {
            "name": sub.name,
            "provider": sub.provider,
            "category": sub.category,
            "kind": sub.kind,
            "plan": sub.plan,
            "amount": sub.amount,
            "currency": sub.currency,
            "cycle": self._to_display("cycle", sub.cycle.value if sub.cycle else None),
            "due": sub.due,
            "status": self._to_display("status", sub.status.value if sub.status else None),
            "auto_renew": sub.auto_renew,
            "notes": sub.notes,
        }
        out: dict[str, Any] = {}
        for logical, value in values.items():
            if only is not None and logical not in only:
                continue
            name = self.fields.get(logical)
            prop = self.schema.get(name or "")
            if not prop:
                continue  # 数据库里没有这个字段，跳过
            if value is None and only is None:
                continue  # 新建时不写空值
            if value is not None and prop["type"] in ("select", "multi_select"):
                for v in value if isinstance(value, list) else [value]:
                    self.ensure_option(name, str(v))
                prop = self.schema[name]
            out[name] = encode_property(prop["type"], value)
        return out

    def create(self, sub: Subscription) -> Subscription:
        page = self.api.create_page(self.ds_id, self._encode(sub))
        sub.page_id = page.get("id")
        return sub

    def update(self, sub: Subscription, changed: set[str]) -> None:
        if not sub.page_id:
            raise NotionError("更新需要 page_id")
        props = self._encode(sub, only=changed)
        if props:
            self.api.update_page(sub.page_id, props)
