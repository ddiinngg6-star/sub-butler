"""命令行入口。"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .config import Config, ConfigError, load_config
from .extract import LLMExtractor
from .fx import get_rates
from .mail import ImapSource
from .notify import build_notifier
from .notion import NotionClient, SubscriptionStore
from .remind import due_items, render_reminder, render_report
from .state import State
from .sync import run_sync


def _today(cfg: Config) -> date:
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def _store(cfg: Config) -> SubscriptionStore:
    client = NotionClient(cfg.notion.token)
    ds_id = cfg.notion.data_source_id
    if not ds_id:
        if not cfg.notion.database_id:
            raise ConfigError("请在配置里填写 notion.data_source_id 或 notion.database_id")
        ds_id = client.resolve_data_source(cfg.notion.database_id)
    return SubscriptionStore(client, ds_id, cfg.notion)


def _state(cfg: Config) -> State:
    return State(Path(cfg.state_dir) / "state.db")


def cmd_check(cfg: Config, args) -> int:
    ok = True
    print("① Notion ...", end=" ", flush=True)
    try:
        store = _store(cfg)
        missing = store.check_fields()
        subs = store.load()
        print(f"OK，读取到 {len(subs)} 条订阅")
        if missing:
            print("   ⚠️ 这些字段在数据库里找不到，将被跳过：" + "；".join(missing))
    except Exception as e:
        ok = False
        print(f"失败：{e}")

    print("② 邮箱 IMAP ...", end=" ", flush=True)
    try:
        n = ImapSource(cfg.mail).check()
        print(f"OK，「{cfg.mail.folder}」里有 {n} 封邮件")
    except Exception as e:
        ok = False
        print(f"失败：{e}")

    print("③ 大模型 ...", end=" ", flush=True)
    try:
        from .models import MailMessage

        ev = LLMExtractor(cfg.llm).extract(
            MailMessage("check", "billing@example.com", "Your receipt", _today(cfg), "Receipt: Example Pro, $10.00 USD, monthly."),
            {"providers": [], "categories": [], "kinds": []},
        )
        print(f"OK（测试解析结果：{ev.event.value}）")
    except Exception as e:
        ok = False
        print(f"失败：{e}")
    return 0 if ok else 1


def cmd_sync(cfg: Config, args) -> int:
    since = date.fromisoformat(args.since) if args.since else None
    report = run_sync(
        mail=ImapSource(cfg.mail, cfg.llm.max_chars),
        extractor=LLMExtractor(cfg.llm),
        store=_store(cfg),
        state=_state(cfg),
        today=_today(cfg),
        lookback_days=cfg.mail.lookback_days,
        since=since,
        dry_run=args.dry_run,
    )
    text = report.render()
    if args.dry_run:
        print("（试运行，没有写入 Notion）")
        print(text)
    elif report.has_news:
        build_notifier(cfg.notify).send("订阅同步", text)
    else:
        print(text)
    return 1 if report.errors else 0


def _remind(cfg: Config, store: SubscriptionStore, days: int, send: bool) -> None:
    today = _today(cfg)
    upcoming, overdue = due_items(store.load(), today, days)
    if not cfg.remind.include_overdue:
        overdue = []
    text = render_reminder(today, upcoming, overdue)
    if not text:
        print("没有即将到期的订阅。")
    elif send:
        build_notifier(cfg.notify).send("订阅提醒", text)
    else:
        print(text)


def cmd_remind(cfg: Config, args) -> int:
    _remind(cfg, _store(cfg), args.days or cfg.remind.window_days, send=not args.dry_run)
    return 0


def cmd_report(cfg: Config, args) -> int:
    rates = get_rates(cfg.state_dir, cfg.report.fallback_rates)
    text = render_report(_store(cfg).load(), _today(cfg), cfg.report.base_currency.upper(), rates)
    if args.send:
        build_notifier(cfg.notify).send("订阅月报", text)
    else:
        print(text)
    return 0


def cmd_run(cfg: Config, args) -> int:
    """每天跑一次：同步邮件 → 到期提醒 →（每月指定日）月报。适合放进 cron。"""
    store = _store(cfg)
    notifier = build_notifier(cfg.notify)
    today = _today(cfg)
    report = run_sync(
        mail=ImapSource(cfg.mail, cfg.llm.max_chars),
        extractor=LLMExtractor(cfg.llm),
        store=store,
        state=_state(cfg),
        today=today,
        lookback_days=cfg.mail.lookback_days,
    )
    if report.has_news:
        notifier.send("订阅同步", report.render())
    _remind(cfg, store, cfg.remind.window_days, send=True)
    if today.day == cfg.report.monthly_day:
        rates = get_rates(cfg.state_dir, cfg.report.fallback_rates)
        notifier.send("订阅月报", render_report(store.load(), today, cfg.report.base_currency.upper(), rates))
    return 1 if report.errors else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sub-butler", description="订阅账单自动管家")
    p.add_argument("-c", "--config", default="config.yaml", help="配置文件路径（默认 config.yaml）")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="检查 Notion、邮箱和大模型配置是否可用")

    s = sub.add_parser("sync", help="扫描账单邮件并同步到 Notion")
    s.add_argument("--dry-run", action="store_true", help="只打印结果，不写入 Notion")
    s.add_argument("--since", help="从这一天开始扫描（YYYY-MM-DD），默认从上次同步开始")

    r = sub.add_parser("remind", help="发送即将到期的提醒")
    r.add_argument("--days", type=int, help="提前几天提醒")
    r.add_argument("--dry-run", action="store_true", help="只打印，不推送")

    rp = sub.add_parser("report", help="生成订阅月报")
    rp.add_argument("--send", action="store_true", help="推送到通知渠道（默认只打印）")

    sub.add_parser("run", help="每日任务：同步 + 提醒 + 月报（适合 cron）")
    return p


COMMANDS = {"check": cmd_check, "sync": cmd_sync, "remind": cmd_remind, "report": cmd_report, "run": cmd_run}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    try:
        cfg = load_config(args.config)
        return COMMANDS[args.command](cfg, args)
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2
