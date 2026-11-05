from datetime import date

from fakes import FakeExtractor, FakeMail, FakeNotion, mail, seed
from sub_butler.config import NotionConfig
from sub_butler.models import BillingEvent, Cycle, EventType, Status, Subscription
from sub_butler.notion import SubscriptionStore
from sub_butler.state import State
from sub_butler.sync import apply_event, find_match, rollover, run_sync

TODAY = date(2026, 11, 4)


def _run(fake, messages, events, **kw):
    store = SubscriptionStore(fake, "ds", NotionConfig())
    state = kw.pop("state", None) or State(":memory:")
    return run_sync(
        mail=FakeMail(messages),
        extractor=FakeExtractor(events),
        store=store,
        state=state,
        today=TODAY,
        **kw,
    ), store, state


def test_new_purchase_creates_row():
    fake = FakeNotion()
    ev = BillingEvent(
        EventType.PURCHASE, provider="CloudCo", product="美国 VPS", plan="2C2G", category="VPS",
        kind="订阅", amount=4.99, currency="USD", cycle=Cycle.MONTHLY, event_date=date(2026, 11, 3),
        summary="新购美国 VPS，实付 $4.99",
    )
    report, store, state = _run(fake, [mail("m1")], {"m1": ev})

    assert len(report.changes) == 1 and report.changes[0].created
    subs = store.load()
    assert len(subs) == 1
    s = subs[0]
    assert s.name == "CloudCo 美国 VPS"
    assert s.due == date(2026, 12, 3)  # 按月付推算
    assert s.status == Status.ACTIVE
    assert s.amount == 4.99 and s.currency == "USD"
    assert "新购美国 VPS" in s.notes
    assert state.seen("m1")


def test_renewal_updates_existing_row_due_date():
    fake = FakeNotion()
    seed(fake, name="ExampleAI Pro", provider="ExampleAI", category="AI", amount=20, currency="USD",
         cycle=Cycle.MONTHLY, due=date(2026, 10, 27), status=Status.ACTIVE, auto_renew=True)
    ev = BillingEvent(EventType.RENEWAL, provider="ExampleAI", product="Pro", amount=20, currency="USD",
                      cycle=Cycle.MONTHLY, event_date=date(2026, 10, 26), next_due=date(2026, 11, 26),
                      summary="Pro 续费 $20")
    report, store, _ = _run(fake, [mail("m1")], {"m1": ev})

    subs = store.load()
    assert len(subs) == 1, "续费不应该新建一行"
    assert subs[0].due == date(2026, 11, 26)
    assert not report.changes[0].created
    # 只更新了改动的字段
    kind, props = fake.calls[0]
    assert kind == "update"
    assert "续费/到期日" in props and "产品名称" not in props


def test_not_billing_is_skipped_and_remembered():
    fake = FakeNotion()
    report, store, state = _run(fake, [mail("m1")], {"m1": BillingEvent(EventType.NOT_BILLING)})
    assert report.billing == 0 and not report.changes
    assert store.load() == []
    assert state.seen("m1")


def test_already_processed_messages_are_ignored():
    fake = FakeNotion()
    state = State(":memory:")
    state.mark("m1", "renewal")
    report, _, _ = _run(fake, [mail("m1")], {}, state=state)
    assert report.scanned == 0


def test_extract_error_does_not_mark_seen_so_it_retries():
    fake = FakeNotion()
    report, _, state = _run(fake, [mail("m1")], {"m1": RuntimeError("timeout")})
    assert report.errors and not state.seen("m1")


def test_dry_run_writes_nothing():
    fake = FakeNotion()
    ev = BillingEvent(EventType.PURCHASE, provider="CloudCo", product="VPS", amount=5, currency="USD", cycle=Cycle.MONTHLY)
    report, _, state = _run(fake, [mail("m1")], {"m1": ev}, dry_run=True)
    assert report.changes and fake.pages == {}
    assert not state.seen("m1") and state.last_sync is None


def test_trial_and_credit_exhausted():
    fake = FakeNotion()
    seed(fake, name="ExampleAI API", provider="ExampleAI", kind="API 额度", cycle=Cycle.USAGE, status=Status.ACTIVE)
    trial = BillingEvent(EventType.TRIAL_STARTED, provider="CodeTool", product="Pro 试用", category="AI",
                         next_due=date(2026, 11, 10), summary="Pro 试用 2 周")
    empty = BillingEvent(EventType.CREDIT_EXHAUSTED, provider="ExampleAI", product="API", summary="API 额度已用完")
    report, store, _ = _run(fake, [mail("t"), mail("c")], {"t": trial, "c": empty})

    by_name = {s.name: s for s in store.load()}
    assert by_name["CodeTool Pro 试用"].status == Status.TRIAL
    assert by_name["CodeTool Pro 试用"].due == date(2026, 11, 10)
    assert by_name["ExampleAI API"].status == Status.EXPIRED
    assert any("额度" in a for a in report.alerts)


def test_rollover_auto_renew_past_due():
    fake = FakeNotion()
    seed(fake, name="CloudCo 日本 VPS", provider="CloudCo", cycle=Cycle.MONTHLY, due=date(2026, 11, 1),
         status=Status.ACTIVE, auto_renew=True)
    seed(fake, name="手机号保号", cycle=Cycle.MONTHLY, due=date(2026, 11, 1), status=Status.ACTIVE, auto_renew=False)
    report, store, _ = _run(fake, [], {})

    by_name = {s.name: s for s in store.load()}
    assert by_name["CloudCo 日本 VPS"].due == date(2026, 12, 1)
    assert by_name["手机号保号"].due == date(2026, 11, 1), "手动续费的不自动推日期"
    assert len(report.rolled) == 1


def test_find_match_prefers_same_product():
    subs = [
        Subscription(name="CloudCo 日本 VPS", provider="CloudCo", amount=5, status=Status.ACTIVE),
        Subscription(name="CloudCo 美国 VPS", provider="CloudCo", amount=6, status=Status.ACTIVE),
    ]
    ev = BillingEvent(EventType.RENEWAL, provider="cloudco", product="美国 VPS", amount=6)
    assert find_match(ev, subs) is subs[1]


def test_find_match_different_provider_returns_none():
    subs = [Subscription(name="ExampleAI Pro", provider="ExampleAI", status=Status.ACTIVE)]
    ev = BillingEvent(EventType.PURCHASE, provider="CloudCo", product="Pro")
    assert find_match(ev, subs) is None


def test_find_match_ambiguous_new_product_creates_new():
    subs = [
        Subscription(name="CloudCo 日本 VPS", provider="CloudCo", amount=5, status=Status.ACTIVE),
        Subscription(name="CloudCo 美国 VPS", provider="CloudCo", amount=6, status=Status.ACTIVE),
    ]
    ev = BillingEvent(EventType.PURCHASE, provider="CloudCo", product="新加坡 独立服务器", amount=50)
    assert find_match(ev, subs) is None


def test_apply_event_does_not_mutate_existing_and_dedupes_notes():
    existing = Subscription(name="ExampleAI Pro", provider="ExampleAI", notes="[2026-10-26] Pro 续费 $20（自动同步）")
    ev = BillingEvent(EventType.RENEWAL, provider="ExampleAI", product="Pro", event_date=date(2026, 10, 26),
                      summary="Pro 续费 $20")
    sub, changed, _ = apply_event(ev, existing, TODAY)
    assert existing.status is None, "原对象不应被修改"
    assert "notes" not in changed, "同一条备注不重复追加"
    assert sub.status == Status.ACTIVE


def test_context_passes_existing_options_to_extractor():
    fake = FakeNotion()
    extractor = FakeExtractor({"m1": BillingEvent(EventType.NOT_BILLING)})
    run_sync(mail=FakeMail([mail("m1")]), extractor=extractor, store=SubscriptionStore(fake, "ds", NotionConfig()),
             state=State(":memory:"), today=TODAY)
    ctx = extractor.contexts[0]
    assert "VPS" in ctx["categories"] and "CloudCo" in ctx["providers"]


def test_since_defaults_to_last_sync():
    fake = FakeNotion()
    state = State(":memory:")
    state.last_sync = date(2026, 11, 1)
    m = FakeMail([])
    run_sync(mail=m, extractor=FakeExtractor({}), store=SubscriptionStore(fake, "ds", NotionConfig()),
             state=state, today=TODAY)
    assert m.since == date(2026, 11, 1)
    assert state.last_sync == TODAY
