"""配置加载：YAML 文件 + `${ENV_VAR}` 环境变量替换。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 默认字段名：与作者自己的 Notion「订阅与权益」库一致。英文库可在配置里覆盖。
DEFAULT_FIELDS: dict[str, str] = {
    "name": "产品名称",
    "provider": "服务商",
    "category": "分类",
    "kind": "类型",
    "plan": "套餐/等级",
    "amount": "费用",
    "currency": "币种",
    "cycle": "计费周期",
    "due": "续费/到期日",
    "status": "状态",
    "auto_renew": "自动续费",
    "notes": "备注",
    "account": "登录账号",
    "payment_method": "支付方式",
    "manage_url": "管理链接",
}

DEFAULT_VALUES: dict[str, dict[str, str]] = {
    "cycle": {
        "monthly": "月付",
        "bimonthly": "每两月",
        "quarterly": "季付",
        "yearly": "年付",
        "usage": "按量",
        "once": "一次性",
    },
    "status": {
        "active": "使用中",
        "trial": "试用中",
        "cancelled": "已取消",
        "expired": "已过期",
    },
}

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    pass


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class MailConfig:
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    username: str = ""
    password: str = ""
    folder: str = "INBOX"
    lookback_days: int = 7
    max_messages: int = 200
    include_senders: list[str] = field(default_factory=list)
    exclude_senders: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"
    max_chars: int = 6000
    timeout: float = 60.0


@dataclass
class NotionConfig:
    token: str = ""
    data_source_id: str = ""
    database_id: str = ""
    fields: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FIELDS))
    values: dict[str, dict[str, str]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_VALUES.items()}
    )


@dataclass
class RemindConfig:
    window_days: int = 3
    include_overdue: bool = True


@dataclass
class ReportConfig:
    base_currency: str = "CNY"
    fallback_rates: dict[str, float] = field(
        default_factory=lambda: {"USD": 1.0, "CNY": 7.1, "HKD": 7.8, "TWD": 32.0, "EUR": 0.92}
    )
    monthly_day: int = 1


@dataclass
class Config:
    timezone: str = "Asia/Shanghai"
    state_dir: str = "./data"
    mail: MailConfig = field(default_factory=MailConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    notion: NotionConfig = field(default_factory=NotionConfig)
    remind: RemindConfig = field(default_factory=RemindConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    notify: dict[str, dict[str, Any]] = field(default_factory=dict)


def _build(cls, data: dict[str, Any] | None):
    data = data or {}
    known = set(cls.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{cls.__name__} 里有未知配置项：{', '.join(sorted(unknown))}")
    return cls(**data)


def load_dotenv(path: str | Path) -> None:
    """读取 KEY=VALUE 格式的 .env；已存在的环境变量优先，不会被覆盖。"""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"找不到配置文件 {p}，可以先复制 config.example.yaml 再修改。")
    load_dotenv(p.parent / ".env")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = _expand_env(raw)
    return config_from_dict(raw)


def config_from_dict(raw: dict[str, Any]) -> Config:
    raw = dict(raw)
    notion_raw = dict(raw.pop("notion", {}) or {})
    # 字段和取值映射：在默认值基础上合并，用户只需写想改的部分
    fields = dict(DEFAULT_FIELDS)
    fields.update(notion_raw.pop("fields", {}) or {})
    values = {k: dict(v) for k, v in DEFAULT_VALUES.items()}
    for k, v in (notion_raw.pop("values", {}) or {}).items():
        values.setdefault(k, {}).update(v or {})
    notion = _build(NotionConfig, notion_raw)
    notion.fields = fields
    notion.values = values

    cfg = Config(
        timezone=raw.pop("timezone", "Asia/Shanghai"),
        state_dir=raw.pop("state_dir", "./data"),
        mail=_build(MailConfig, raw.pop("mail", None)),
        llm=_build(LLMConfig, raw.pop("llm", None)),
        notion=notion,
        remind=_build(RemindConfig, raw.pop("remind", None)),
        report=_build(ReportConfig, raw.pop("report", None)),
        notify={k: v for k, v in (raw.pop("notify", {}) or {}).items() if v},
    )
    if raw:
        raise ConfigError(f"未知的顶层配置项：{', '.join(sorted(raw))}")
    return cfg
