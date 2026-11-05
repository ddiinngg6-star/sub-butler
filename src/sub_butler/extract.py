"""用大模型（任意 OpenAI 兼容接口，默认 DeepSeek）从邮件中提取账单信息。"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Protocol

import httpx

from .config import LLMConfig
from .models import BillingEvent, Cycle, EventType, MailMessage

SYSTEM_PROMPT = """你是一个账单邮件解析器。用户会给你一封邮件，你要判断它是否和用户的某个订阅、服务器、会员、API 额度、话费等付费服务有关，并提取结构化信息。

只输出一个 JSON 对象，字段如下：
{
  "event": 以下之一：
     "purchase"          新购买 / 新开通（含服务器交付、订单付款成功）
     "renewal"           周期续费成功 / 周期扣款收据
     "trial_started"     开始免费试用
     "renewal_notice"    即将续费或即将到期的提醒（尚未扣款）
     "cancelled"         订阅已取消
     "expired"           已到期 / 服务被暂停或删除
     "payment_failed"    扣款失败
     "credit_exhausted"  预付额度或余额用完 / 不足
     "not_billing"       与付费服务无关：营销促销、验证码、登录提醒、新闻通讯、未付款的账单生成通知等
  "provider": 服务商名称，尽量用已有服务商列表里的写法，例如 "Vultr"、"Anthropic"
  "product": 简短的产品名，例如 "Claude Pro"、"美国 VPS"
  "plan": 套餐 / 配置 / 等级，没有就 null
  "category": 从给定的分类列表里选一个，都不合适就 null
  "kind": 从给定的类型列表里选一个，都不合适就 null
  "amount": 实际支付金额（扣除优惠后），数字；未知为 null
  "currency": ISO 货币代码，如 "USD"、"CNY"、"HKD"
  "cycle": "monthly" | "bimonthly" | "quarterly" | "yearly" | "usage" | "once" | null
  "event_date": 扣款 / 开通 / 发生日期，YYYY-MM-DD
  "next_due": 下次续费日或到期日（试用则为试用结束日），YYYY-MM-DD；邮件里没写就根据周期推算，推算不了为 null
  "auto_renew": 是否自动续费，true / false / null
  "summary": 一句中文摘要，不超过 40 字，不要包含卡号、密码、验证码、IP 等敏感信息
}
注意：只生成了账单但还没付款的通知，算 not_billing；同一笔订单通常还会有付款收据邮件。"""


class Extractor(Protocol):
    def extract(self, msg: MailMessage, context: dict[str, list[str]]) -> BillingEvent: ...


class ExtractError(Exception):
    pass


def build_user_prompt(msg: MailMessage, context: dict[str, list[str]]) -> str:
    lines = [
        f"已有服务商：{', '.join(context.get('providers', [])) or '（无）'}",
        f"分类列表：{', '.join(context.get('categories', [])) or '（无）'}",
        f"类型列表：{', '.join(context.get('kinds', [])) or '（无）'}",
        "",
        f"发件人：{msg.sender}",
        f"日期：{msg.date.isoformat() if msg.date else '未知'}",
        f"主题：{msg.subject}",
        "正文：",
        msg.text,
    ]
    return "\n".join(lines)


def _parse_date(v: Any) -> date | None:
    if not v or not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _parse_amount(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def _pick(value: Any, allowed: list[str]) -> str | None:
    """把大模型给的值对齐到已有选项（忽略大小写）；对不上就原样保留。"""
    if not value:
        return None
    s = str(value).strip()
    for opt in allowed:
        if opt.lower() == s.lower():
            return opt
    return s


def parse_event(data: dict[str, Any], msg: MailMessage, context: dict[str, list[str]]) -> BillingEvent:
    try:
        event = EventType(str(data.get("event", "not_billing")).strip())
    except ValueError:
        event = EventType.NOT_BILLING
    cycle = None
    if data.get("cycle"):
        try:
            cycle = Cycle(str(data["cycle"]).strip())
        except ValueError:
            cycle = None
    currency = str(data["currency"]).strip().upper() if data.get("currency") else None
    auto = data.get("auto_renew")
    return BillingEvent(
        event=event,
        provider=_pick(data.get("provider"), context.get("providers", [])) or "",
        product=str(data.get("product") or "").strip(),
        plan=(str(data["plan"]).strip() or None) if data.get("plan") else None,
        category=_pick(data.get("category"), context.get("categories", [])),
        kind=_pick(data.get("kind"), context.get("kinds", [])),
        amount=_parse_amount(data.get("amount")),
        currency=currency,
        cycle=cycle,
        event_date=_parse_date(data.get("event_date")) or msg.date,
        next_due=_parse_date(data.get("next_due")),
        auto_renew=auto if isinstance(auto, bool) else None,
        summary=str(data.get("summary") or "").strip()[:80],
        source=msg,
    )


def _extract_json(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            return json.loads(m.group())
        raise


class LLMExtractor:
    def __init__(self, cfg: LLMConfig, http: httpx.Client | None = None):
        if not cfg.api_key:
            raise ExtractError("缺少大模型 API Key（llm.api_key）")
        self.cfg = cfg
        self._http = http or httpx.Client(timeout=cfg.timeout)

    def extract(self, msg: MailMessage, context: dict[str, list[str]]) -> BillingEvent:
        resp = self._http.post(
            self.cfg.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json={
                "model": self.cfg.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(msg, context)},
                ],
            },
        )
        if resp.status_code >= 400:
            raise ExtractError(f"大模型接口返回 {resp.status_code}：{resp.text[:300]}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            data = _extract_json(content)
        except (KeyError, IndexError, ValueError) as e:
            raise ExtractError(f"无法解析大模型输出：{e}") from e
        return parse_event(data, msg, context)
