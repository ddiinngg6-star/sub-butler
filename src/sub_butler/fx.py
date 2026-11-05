"""汇率：优先在线获取（免费、无需 Key），失败时用配置里的备用汇率。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

RATES_URL = "https://open.er-api.com/v6/latest/USD"
CACHE_SECONDS = 24 * 3600


def get_rates(cache_dir: str | Path, fallback: dict[str, float], http: httpx.Client | None = None) -> dict[str, float]:
    """返回以 USD 为 1 的汇率表。"""
    cache = Path(cache_dir) / "rates.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_SECONDS:
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    try:
        client = http or httpx.Client(timeout=15)
        data = client.get(RATES_URL).json()
        if data.get("result") == "success" and data.get("rates"):
            rates = {k.upper(): float(v) for k, v in data["rates"].items()}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(rates))
            return rates
    except Exception as e:  # 网络问题不影响主流程
        log.warning("获取汇率失败，使用备用汇率：%s", e)
    return {k.upper(): float(v) for k, v in fallback.items()}
