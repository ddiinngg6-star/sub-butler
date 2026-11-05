import json
from datetime import date

import httpx
import pytest

from fakes import mail
from sub_butler.config import LLMConfig
from sub_butler.extract import ExtractError, LLMExtractor, parse_event
from sub_butler.models import Cycle, EventType

CTX = {"providers": ["Vultr", "ExampleAI"], "categories": ["AI", "VPS"], "kinds": ["订阅", "API 额度"]}


def _client(content: str, status: int = 200, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_llm_extractor_parses_json_and_normalizes():
    payload = {
        "event": "purchase", "provider": "vultr", "product": "日本 VPS", "plan": "1C1G",
        "category": "vps", "kind": "订阅", "amount": "$5.00", "currency": "usd", "cycle": "monthly",
        "event_date": "2026-11-01", "next_due": "2026-12-01", "auto_renew": True, "summary": "Vultr 日本 VPS 月付 $5",
    }
    seen = []
    ex = LLMExtractor(LLMConfig(api_key="k"), http=_client(json.dumps(payload), seen=seen))
    ev = ex.extract(mail("m1", subject="New Invoice"), CTX)

    assert ev.event == EventType.PURCHASE
    assert ev.provider == "Vultr" and ev.category == "VPS"  # 对齐到已有选项的大小写
    assert ev.amount == 5.0 and ev.currency == "USD" and ev.cycle == Cycle.MONTHLY
    assert ev.next_due == date(2026, 12, 1) and ev.auto_renew is True
    req = seen[0]
    assert req["response_format"] == {"type": "json_object"} and req["temperature"] == 0
    assert "已有服务商：Vultr, ExampleAI" in req["messages"][1]["content"]


def test_llm_extractor_handles_fenced_json():
    ex = LLMExtractor(LLMConfig(api_key="k"), http=_client('```json\n{"event": "not_billing"}\n```'))
    assert ex.extract(mail("m1"), CTX).event == EventType.NOT_BILLING


def test_llm_extractor_http_error():
    ex = LLMExtractor(LLMConfig(api_key="k"), http=_client("", status=401))
    with pytest.raises(ExtractError):
        ex.extract(mail("m1"), CTX)


def test_missing_api_key():
    with pytest.raises(ExtractError):
        LLMExtractor(LLMConfig(api_key=""))


def test_parse_event_tolerates_garbage():
    ev = parse_event({"event": "weird", "cycle": "fortnightly", "amount": "abc", "next_due": "soon"}, mail("m1"), CTX)
    assert ev.event == EventType.NOT_BILLING
    assert ev.cycle is None and ev.amount is None and ev.next_due is None
    assert ev.event_date == date(2026, 11, 3)  # 回退到邮件日期
