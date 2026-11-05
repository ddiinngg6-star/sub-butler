"""用一个假的 imaplib 连接测试 ImapSource 的完整流程。"""

from datetime import date
from email.message import EmailMessage

from sub_butler import mail as mail_mod
from sub_butler.config import MailConfig
from sub_butler.mail import ImapSource


def _raw(subject: str, sender: str, body: str, mid: str) -> bytes:
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = sender, subject, mid
    m["Date"] = "Tue, 03 Nov 2026 09:00:00 +0000"
    m.set_content(body)
    return m.as_bytes()


MAILBOX = {
    b"101": _raw("账单付款确认", "support@cloud.example", "总计: $4.99 USD", "<a@x>"),
    b"102": _raw("Save 50% today", "promo@shop.example", "sale", "<b@x>"),
    b"103": _raw("Your receipt", "invoice@ai.example", "Claude Pro $20", "<c@x>"),
    b"104": _raw("验证码", "noreply@ai.example", "123456", "<d@x>"),
}


class FakeIMAP:
    def __init__(self, host, port):
        self.searched = None
        self.fetched_bodies = []

    def login(self, user, pw):
        return "OK", [b"ok"]

    def select(self, folder, readonly=False):
        assert readonly, "必须以只读方式打开邮件夹"
        self.folder = folder
        return "OK", [str(len(MAILBOX)).encode()]

    def uid(self, cmd, *args):
        if cmd == "search":
            self.searched = args
            return "OK", [b" ".join(MAILBOX)]
        if cmd == "fetch":
            ids, what = args
            if "HEADER.FIELDS" in what:
                rows = []
                for n, uid in enumerate(ids.split(b","), 1):
                    hdr = b"\r\n".join(
                        line for line in MAILBOX[uid].split(b"\n")
                        if line.startswith((b"From:", b"Subject:"))
                    ) + b"\r\n\r\n"
                    if uid == b"103":  # 模拟 UID 放在末尾的服务器
                        rows += [(f"{n} (BODY[HEADER.FIELDS (FROM SUBJECT)] {{{len(hdr)}}}".encode(), hdr), f" UID {uid.decode()})".encode()]
                    else:
                        rows += [(f"{n} (UID {uid.decode()} BODY[HEADER.FIELDS (FROM SUBJECT)] {{{len(hdr)}}}".encode(), hdr), b")"]
                return "OK", rows
            self.fetched_bodies.append(ids)
            assert "PEEK" in what, "不能把邮件标记为已读"
            return "OK", [(b"1 (BODY[] {10}", MAILBOX[ids]), b")"]
        raise AssertionError(cmd)

    def logout(self):
        pass


def test_imap_fetch_prefilters_and_parses(monkeypatch):
    fake = {}

    def factory(host, port):
        fake["conn"] = FakeIMAP(host, port)
        return fake["conn"]

    monkeypatch.setattr(mail_mod.imaplib, "IMAP4_SSL", factory)
    src = ImapSource(MailConfig(username="u", password="p", folder="[Gmail]/All Mail"))
    msgs = src.fetch(date(2026, 11, 3))

    conn = fake["conn"]
    assert conn.folder == '"[Gmail]/All Mail"'
    assert conn.searched == (None, "SINCE", "02-Nov-2026")
    # 只有两封账单邮件被下载正文：促销和验证码在预筛阶段就被跳过
    assert conn.fetched_bodies == [b"101", b"103"]
    assert [m.subject for m in msgs] == ["账单付款确认", "Your receipt"]
    assert msgs[0].message_id == "<a@x>" and "$4.99" in msgs[0].text
