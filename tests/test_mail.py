from email.message import EmailMessage

from sub_butler.config import MailConfig
from sub_butler.mail import clean_text, html_to_text, looks_like_billing, parse_message


def test_prefilter():
    cfg = MailConfig(exclude_senders=["exchange.example"], include_senders=["myhost.example"])
    assert looks_like_billing("billing@cloud.example", "New Invoice", cfg)
    assert looks_like_billing("support@cloud.example", "账单付款确认", cfg)
    assert looks_like_billing("noreply@tool.example", "Welcome! Here's your free Pro trial", cfg)
    assert not looks_like_billing("noreply@ai.example", "DeepSeek 验证码", cfg)
    assert not looks_like_billing("hello@mail.example", "Save 50% on Pro today", cfg)
    assert not looks_like_billing("noreply@exchange.example", "Withdrawal payment successful", cfg)
    assert looks_like_billing("x@myhost.example", "Hello", cfg)


def test_html_to_text_strips_style_and_keeps_structure():
    html = "<html><head><style>p{color:red}</style></head><body><p>Total</p><table><tr><td>$4.99</td><td>USD</td></tr></table></body></html>"
    text = html_to_text(html)
    assert "color" not in text
    assert "Total" in text and "$4.99" in text


def test_clean_text_removes_tracking_links_and_invisible_chars():
    text = "Pay here https://track.example/ls/click?upn=abc123͏​ now\n\n\n\nThanks"
    out = clean_text(text)
    assert "track.example" not in out and "<url>" in out
    assert "͏" not in out and "\n\n" not in out


def test_parse_message_multipart_prefers_plain():
    m = EmailMessage()
    m["From"] = "Cloud Co <billing@cloud.example>"
    m["Subject"] = "=?utf-8?b?6LSm5Y2V5LuY5qy+56Gu6K6k?="  # 账单付款确认
    m["Date"] = "Tue, 03 Nov 2026 09:12:58 +0000"
    m["Message-ID"] = "<abc@cloud.example>"
    m.set_content("总计: $4.99 USD")
    m.add_alternative("<p>HTML version</p>", subtype="html")
    msg = parse_message(m.as_bytes(), "uid-1")
    assert msg.sender == "billing@cloud.example"
    assert msg.subject == "账单付款确认"
    assert msg.message_id == "<abc@cloud.example>"
    assert msg.date.isoformat() == "2026-11-03"
    assert "$4.99" in msg.text and "HTML version" not in msg.text


def test_parse_message_html_only():
    m = EmailMessage()
    m["From"] = "billing@cloud.example"
    m["Subject"] = "Receipt"
    m.set_content("<div>Amount paid <b>$20.00</b></div>", subtype="html")
    msg = parse_message(m.as_bytes(), "uid-2")
    assert msg.message_id == "uid-2"
    assert "Amount paid" in msg.text and "$20.00" in msg.text
