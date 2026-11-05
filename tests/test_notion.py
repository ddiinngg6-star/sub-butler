import json
from datetime import date

import httpx
import pytest

from fakes import FakeNotion, seed
from sub_butler.config import NotionConfig, config_from_dict
from sub_butler.models import Cycle, Status
from sub_butler.notion import NotionClient, NotionError, SubscriptionStore, decode_property, encode_property


def test_encode_decode_roundtrip():
    assert encode_property("select", "VPS") == {"select": {"name": "VPS"}}
    assert encode_property("select", None) == {"select": None}
    assert encode_property("date", date(2026, 12, 3)) == {"date": {"start": "2026-12-03"}}
    assert encode_property("checkbox", 1) == {"checkbox": True}
    assert encode_property("rich_text", "x" * 3000)["rich_text"][0]["text"]["content"] == "x" * 2000
    assert decode_property({"type": "date", "date": {"start": "2026-12-03T00:00:00.000+08:00"}}) == date(2026, 12, 3)
    assert decode_property({"type": "title", "title": [{"plain_text": "A"}, {"plain_text": "B"}]}) == "AB"
    assert decode_property({"type": "select", "select": None}) is None


def test_store_maps_chinese_display_values():
    fake = FakeNotion()
    seed(fake, name="X", cycle=Cycle.BIMONTHLY, status=Status.TRIAL)
    props = next(iter(fake.pages.values()))
    assert props["计费周期"]["select"]["name"] == "每两月"
    assert props["状态"]["select"]["name"] == "试用中"
    sub = SubscriptionStore(fake, "ds", NotionConfig()).load()[0]
    assert sub.cycle == Cycle.BIMONTHLY and sub.status == Status.TRIAL


def test_store_skips_fields_missing_from_schema():
    cfg = config_from_dict({"notion": {"fields": {"plan": "不存在的字段"}}}).notion
    store = SubscriptionStore(FakeNotion(), "ds", cfg)
    assert any("不存在的字段" in m for m in store.check_fields())


def test_custom_english_schema_values():
    cfg = config_from_dict({"notion": {"values": {"status": {"active": "Active"}}}}).notion
    assert cfg.values["status"]["active"] == "Active"
    assert cfg.values["status"]["trial"] == "试用中"  # 其余保留默认
    assert cfg.fields["name"] == "产品名称"


def test_client_uses_data_source_endpoints_and_paginates():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        assert req.headers["Notion-Version"] == "2025-09-03"
        if req.url.path.endswith("/query"):
            body = json.loads(req.content)
            if "start_cursor" not in body:
                return httpx.Response(200, json={"results": [{"id": "a"}], "has_more": True, "next_cursor": "c1"})
            return httpx.Response(200, json={"results": [{"id": "b"}], "has_more": False})
        if req.url.path == "/v1/pages":
            return httpx.Response(200, json={"id": "new"})
        return httpx.Response(404, json={"message": "nope"})

    client = NotionClient("secret", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [r["id"] for r in client.query_all("ds1")] == ["a", "b"]
    client.create_page("ds1", {"产品名称": {"title": []}})
    assert calls[-1][2]["parent"] == {"type": "data_source_id", "data_source_id": "ds1"}
    with pytest.raises(NotionError, match="nope"):
        client.get_data_source("missing")


def test_client_requires_token():
    with pytest.raises(NotionError):
        NotionClient("")


def test_new_select_option_is_added_before_write():
    from sub_butler.models import Subscription

    fake = FakeNotion()
    store = SubscriptionStore(fake, "ds", NotionConfig())
    store.create(Subscription(name="NewHost VPS", provider="NewHost", category="VPS"))

    schema_calls = [c for c in fake.calls if c[0] == "schema"]
    assert len(schema_calls) == 1, "只有服务商缺选项，只改一次表结构"
    sent = schema_calls[0][1]["服务商"]["select"]["options"]
    assert sent[:2] == [
        {"id": "服务商-0", "name": "ExampleAI", "color": "blue"},
        {"id": "服务商-1", "name": "CloudCo", "color": "blue"},
    ], "已有选项按 id 原样保留"
    assert sent[-1] == {"name": "NewHost"}
    assert "NewHost" in store.options("provider")
    # 第二次写同一个服务商不再改表结构
    store.create(Subscription(name="NewHost VPS 2", provider="NewHost"))
    assert len([c for c in fake.calls if c[0] == "schema"]) == 1


def test_schema_update_failure_surfaces_as_sync_error():
    from fakes import FakeExtractor, FakeMail, mail
    from sub_butler.models import BillingEvent, EventType
    from sub_butler.state import State
    from sub_butler.sync import run_sync

    fake = FakeNotion(allow_schema_update=False)
    ev = BillingEvent(EventType.PURCHASE, provider="NewHost", product="VPS", amount=5, currency="USD", cycle=Cycle.MONTHLY)
    state = State(":memory:")
    report = run_sync(mail=FakeMail([mail("m1")]), extractor=FakeExtractor({"m1": ev}),
                      store=SubscriptionStore(fake, "ds", NotionConfig()), state=state, today=date(2026, 11, 4))
    assert fake.pages == {}
    assert report.errors and "写入 Notion 失败" in report.errors[0]
    assert not state.seen("m1"), "写入失败的邮件下次重试"
