"""通过 IMAP 读取邮件，并用关键词预筛出可能的账单邮件。

用 IMAP 而不是 Gmail API：Gmail（应用专用密码）、QQ 邮箱、163、Outlook 都能用，
也不需要去 Google Cloud 建项目。
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Iterable, Protocol

from .config import MailConfig
from .models import MailMessage

BILLING_KEYWORDS = [
    # English
    "receipt", "invoice", "order", "payment", "paid", "billing", "bill", "subscription",
    "renew", "renewal", "trial", "expire", "expiring", "expired", "suspend", "credit",
    "charge", "charged", "purchase", "plan", "welcome to", "instance", "server",
    # 中文
    "账单", "订单", "收据", "发票", "付款", "支付", "续费", "到期", "过期", "试用", "订阅",
    "开通", "扣款", "充值", "余额", "额度", "交付", "实例", "服务器", "会员",
]

# 常见的非账单邮件，直接跳过，省大模型费用
NOISE_KEYWORDS = [
    "verification code", "verify your email", "security code", "验证码", "sign-in", "登录提醒",
    "password reset", "重置密码",
]


class MailSource(Protocol):
    def fetch(self, since: date) -> list[MailMessage]: ...


def looks_like_billing(sender: str, subject: str, cfg: MailConfig) -> bool:
    s = sender.lower()
    subj = subject.lower()
    if any(x.lower() in s for x in cfg.exclude_senders):
        return False
    if any(x.lower() in s for x in cfg.include_senders):
        return True
    if any(k in subj for k in NOISE_KEYWORDS):
        return False
    return any(k in subj for k in BILLING_KEYWORDS)


class _HTMLToText(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _HTMLToText()
    p.feed(html)
    return "".join(p.parts)


_URL_RE = re.compile(r"https?://\S+")
_INVISIBLE_RE = re.compile(r"[͏​-‏⁠﻿­]+")


def clean_text(text: str, max_chars: int = 6000) -> str:
    """去掉追踪链接、不可见字符和多余空白，并截断，节省大模型 token。"""
    text = _URL_RE.sub("<url>", text)
    text = _INVISIBLE_RE.sub("", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:max_chars]


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def message_text(msg: Message) -> str:
    plain, html = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        (plain if ctype == "text/plain" else html).append(text)
    if plain and any(t.strip() for t in plain):
        return "\n".join(plain)
    return html_to_text("\n".join(html))


def parse_message(raw: bytes, fallback_id: str, max_chars: int = 6000) -> MailMessage:
    msg = email.message_from_bytes(raw)
    msg_date = None
    if msg.get("Date"):
        try:
            msg_date = parsedate_to_datetime(msg["Date"]).date()
        except (TypeError, ValueError):
            pass
    return MailMessage(
        message_id=(msg.get("Message-ID") or fallback_id).strip(),
        sender=parseaddr(_decode(msg.get("From")))[1] or _decode(msg.get("From")),
        subject=_decode(msg.get("Subject")),
        date=msg_date,
        text=clean_text(message_text(msg), max_chars),
    )


def _chunks(items: list[bytes], size: int) -> Iterable[list[bytes]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class ImapSource:
    def __init__(self, cfg: MailConfig, max_chars: int = 6000):
        self.cfg = cfg
        self.max_chars = max_chars

    def _connect(self) -> imaplib.IMAP4:
        conn = imaplib.IMAP4_SSL(self.cfg.imap_host, self.cfg.imap_port)
        conn.login(self.cfg.username, self.cfg.password)
        return conn

    def check(self) -> int:
        """登录并返回邮件夹里的邮件数，用于 `check` 命令。"""
        conn = self._connect()
        try:
            typ, data = conn.select(_quote(self.cfg.folder), readonly=True)
            if typ != "OK":
                raise RuntimeError(f"无法打开邮件夹 {self.cfg.folder}")
            return int(data[0])
        finally:
            conn.logout()

    def fetch(self, since: date) -> list[MailMessage]:
        conn = self._connect()
        try:
            typ, _ = conn.select(_quote(self.cfg.folder), readonly=True)
            if typ != "OK":
                raise RuntimeError(f"无法打开邮件夹 {self.cfg.folder}")
            # IMAP 的 SINCE 只精确到天，往前多取一天，重复的由状态库去重
            since_str = (since - timedelta(days=1)).strftime("%d-%b-%Y")
            typ, data = conn.uid("search", None, "SINCE", since_str)
            uids = data[0].split() if data and data[0] else []
            uids = uids[-self.cfg.max_messages :]

            candidates: list[bytes] = []
            for batch in _chunks(uids, 50):
                typ, rows = conn.uid(
                    "fetch", b",".join(batch), "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"
                )
                rows = rows or []
                for i, row in enumerate(rows):
                    if not isinstance(row, tuple):
                        continue
                    # 大多数服务器把 UID 放在前面；有的（如 Gmail）可能放在紧随其后的 b' UID 123)' 里
                    m = re.search(rb"UID (\d+)", row[0])
                    if not m and i + 1 < len(rows) and isinstance(rows[i + 1], bytes):
                        m = re.search(rb"UID (\d+)", rows[i + 1])
                    if not m:
                        continue
                    hdr = email.message_from_bytes(row[1])
                    sender = parseaddr(_decode(hdr.get("From")))[1]
                    if looks_like_billing(sender, _decode(hdr.get("Subject")), self.cfg):
                        candidates.append(m.group(1))

            messages = []
            for uid in candidates:
                typ, rows = conn.uid("fetch", uid, "(BODY.PEEK[])")
                for row in rows or []:
                    if isinstance(row, tuple):
                        messages.append(parse_message(row[1], f"uid-{uid.decode()}", self.max_chars))
            return messages
        finally:
            conn.logout()


def _quote(folder: str) -> str:
    return f'"{folder}"' if " " in folder and not folder.startswith('"') else folder
