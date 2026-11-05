"""通知渠道：Telegram、Bark（iOS）、Server 酱（微信）、邮件、控制台。"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, title: str, body: str) -> None: ...


class ConsoleNotifier:
    def send(self, title: str, body: str) -> None:
        print(f"\n===== {title} =====\n{body}\n")


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, http: httpx.Client | None = None):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = str(chat_id)
        self.http = http or httpx.Client(timeout=15)

    def send(self, title: str, body: str) -> None:
        r = self.http.post(self.url, json={"chat_id": self.chat_id, "text": f"{title}\n\n{body}"[:4000]})
        r.raise_for_status()


class BarkNotifier:
    """Bark：iOS 推送。url 形如 https://api.day.app/你的Key"""

    def __init__(self, url: str, http: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.http = http or httpx.Client(timeout=15)

    def send(self, title: str, body: str) -> None:
        r = self.http.post(self.url, json={"title": title, "body": body, "group": "sub-butler"})
        r.raise_for_status()


class ServerChanNotifier:
    """Server 酱：推送到微信。"""

    def __init__(self, sendkey: str, http: httpx.Client | None = None):
        self.url = f"https://sctapi.ftqq.com/{quote(sendkey)}.send"
        self.http = http or httpx.Client(timeout=15)

    def send(self, title: str, body: str) -> None:
        r = self.http.post(self.url, data={"title": title[:32], "desp": body.replace("\n", "\n\n")})
        r.raise_for_status()


class EmailNotifier:
    def __init__(self, smtp_host: str, username: str, password: str, to: str, smtp_port: int = 465, sender: str | None = None):
        self.host, self.port = smtp_host, int(smtp_port)
        self.username, self.password = username, password
        self.to, self.sender = to, sender or username

    def send(self, title: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = title, self.sender, self.to
        msg.set_content(body)
        with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as s:
            s.login(self.username, self.password)
            s.send_message(msg)


class MultiNotifier:
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, title: str, body: str) -> None:
        for n in self.notifiers:
            try:
                n.send(title, body)
            except Exception as e:  # 一个渠道失败不影响其他渠道
                log.error("通知发送失败（%s）：%s", type(n).__name__, e)


def build_notifier(cfg: dict[str, dict[str, Any]]) -> MultiNotifier:
    builders = {
        "telegram": TelegramNotifier,
        "bark": BarkNotifier,
        "serverchan": ServerChanNotifier,
        "email": EmailNotifier,
    }
    out: list[Notifier] = [ConsoleNotifier()]
    for name, opts in cfg.items():
        if name not in builders:
            raise ValueError(f"未知的通知渠道：{name}（支持 {', '.join(builders)}）")
        out.append(builders[name](**opts))
    return MultiNotifier(out)
