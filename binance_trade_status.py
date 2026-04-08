from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import requests

getcontext().prec = 28

DAY_MS = 24 * 60 * 60 * 1000
MAX_WINDOW_MS = 7 * DAY_MS - 1
MAX_LIMIT = 1000

FIXED_SYMBOL = "XAUTUSDT"
DEFAULT_START_TIME = "2026-04-06T00:00:00Z"
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_BASE_URL = "https://fapi.binance.com"
FIXED_CREDENTIALS_FILE = Path(".env")
API_KEY_CANDIDATES = (
    "BINANCE_API_KEY",
    "binance_perpetual_api_key",
    "API_KEY",
)
API_SECRET_CANDIDATES = (
    "BINANCE_API_SECRET",
    "binance_perpetual_api_secret",
    "API_SECRET",
)


class ConfigError(RuntimeError):
    pass


class BinanceAPIError(RuntimeError):
    def __init__(self, status_code: int, code: Optional[int], message: str):
        super().__init__(
            f"Binance API error (status={status_code}, code={code}): {message}"
        )
        self.status_code = status_code
        self.code = code
        self.message = message


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "統計 Binance Perpetual XAUTUSDT 成交資料：交易量、交易次數、累加手續費、累加虧損。"
        )
    )
    parser.add_argument(
        "--start-time",
        help="開始時間（ISO8601，例如 2026-04-06T00:00:00Z，或毫秒 timestamp）",
        default=DEFAULT_START_TIME,
    )
    parser.add_argument(
        "--end-time",
        help="結束時間（ISO8601 或毫秒 timestamp）；不填則用現在時間",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"未指定 --start-time 時，往前回溯天數（預設 {DEFAULT_LOOKBACK_DAYS}）",
    )
    parser.add_argument(
        "--env-file",
        default=str(FIXED_CREDENTIALS_FILE),
        help="Binance API 憑證 .env 檔路徑（預設為固定路徑）",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="Binance futures API base url"
    )
    parser.add_argument("--recv-window", type=int, default=5000, help="MBX recvWindow")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout 秒數")
    parser.add_argument(
        "--max-retries", type=int, default=3, help="429/5xx 最大重試次數"
    )
    parser.add_argument(
        "--output",
        choices=["json", "text"],
        default="json",
        help="輸出格式",
    )
    parser.add_argument("--save", help="將結果另存成檔案（JSON）")
    return parser.parse_args()


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _parse_datetime_to_ms(value: str) -> int:
    value = value.strip()
    if value.isdigit():
        return int(value)

    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def resolve_time_range(
    start_time_input: Optional[str],
    end_time_input: Optional[str],
    lookback_days: int,
) -> Tuple[int, int]:
    if lookback_days <= 0:
        raise ValueError("lookback_days 必須大於 0")

    now_ms = int(time.time() * 1000)
    end_ms = _parse_datetime_to_ms(end_time_input) if end_time_input else now_ms
    if start_time_input:
        start_ms = _parse_datetime_to_ms(start_time_input)
    else:
        start_ms = end_ms - lookback_days * DAY_MS

    if start_ms > end_ms:
        raise ValueError("開始時間不能晚於結束時間")
    return start_ms, end_ms


def _strip_quotes(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw

    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        return raw[1:-1].strip()

    if "#" in raw:
        hash_index = raw.find("#")
        if hash_index > 0 and raw[hash_index - 1].isspace():
            raw = raw[:hash_index].strip()
    return raw


def _looks_like_hummingbot_encrypted(value: str) -> bool:
    if len(value) < 64 or len(value) % 2 != 0:
        return False
    if not value.lower().startswith("7b22"):
        return False
    hex_chars = set("0123456789abcdefABCDEF")
    return all(ch in hex_chars for ch in value)


def _parse_env_line(line: str) -> Tuple[str, str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return "", ""

    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()

    if "=" not in stripped:
        return "", ""

    key, value = stripped.split("=", 1)
    return key.strip(), _strip_quotes(value)


def load_binance_api_credentials_from_file(credentials_file: Path) -> Tuple[str, str]:
    if not credentials_file.exists():
        raise ConfigError(f"找不到憑證檔：{credentials_file}")

    env_values: Dict[str, str] = {}

    with credentials_file.open("r", encoding="utf-8") as f:
        for line in f:
            key, normalized_value = _parse_env_line(line)
            if key:
                env_values[key] = normalized_value

    api_key = next(
        (
            env_values.get(k, "").strip()
            for k in API_KEY_CANDIDATES
            if env_values.get(k, "").strip()
        ),
        "",
    )
    api_secret = next(
        (
            env_values.get(k, "").strip()
            for k in API_SECRET_CANDIDATES
            if env_values.get(k, "").strip()
        ),
        "",
    )

    if not api_key:
        raise ConfigError("憑證檔缺少 API Key。請在 .env 設定 BINANCE_API_KEY。")
    if not api_secret:
        raise ConfigError("憑證檔缺少 API Secret。請在 .env 設定 BINANCE_API_SECRET。")

    if _looks_like_hummingbot_encrypted(api_key) or _looks_like_hummingbot_encrypted(
        api_secret
    ):
        raise ConfigError(
            ".env 內的 key/secret 看起來是 Hummingbot 加密字串，無法直接用於 Binance API。"
            "請在 .env 改放明文 Binance API key/secret。"
        )

    return api_key, api_secret


class BinancePerpetualClient:
    RETRY_STATUS_CODES = {418, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        recv_window: int,
        timeout: float,
        max_retries: int,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._recv_window = recv_window
        self._timeout = timeout
        self._max_retries = max_retries
        self._time_offset_ms = 0
        self._session = requests.Session()

    def _timestamp_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def sync_server_time(self) -> None:
        url = f"{self._base_url}/fapi/v1/time"
        response = self._session.get(url, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        server_time = int(payload["serverTime"])
        local_time = int(time.time() * 1000)
        self._time_offset_ms = server_time - local_time

    def _signed_get(self, path: str, params: Dict[str, Any]) -> Any:
        base_params = {k: v for k, v in params.items() if v is not None}
        base_params["recvWindow"] = self._recv_window

        headers = {"X-MBX-APIKEY": self._api_key}

        attempt = 0
        did_resync_time = False

        def _build_signed_url() -> str:
            request_params = dict(base_params)
            request_params["timestamp"] = self._timestamp_ms()
            query = urlencode(request_params)
            signature = hmac.new(
                self._api_secret.encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return f"{self._base_url}{path}?{query}&signature={signature}"

        while True:
            url = _build_signed_url()
            try:
                response = self._session.get(
                    url, headers=headers, timeout=self._timeout
                )
            except requests.RequestException as exc:
                if attempt >= self._max_retries:
                    raise RuntimeError(
                        f"HTTP request failed after retries: {exc}"
                    ) from exc
                time.sleep(0.5 * (2**attempt))
                attempt += 1
                continue

            status_code = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = {"msg": response.text[:300]}

            if (
                isinstance(payload, dict)
                and "code" in payload
                and payload.get("code") not in (0, None)
            ):
                err_code = int(payload.get("code"))
                err_msg = str(payload.get("msg", "Unknown error"))

                if err_code == -1021 and not did_resync_time:
                    self.sync_server_time()
                    did_resync_time = True
                    continue

                if (
                    status_code in self.RETRY_STATUS_CODES
                    and attempt < self._max_retries
                ):
                    time.sleep(0.5 * (2**attempt))
                    attempt += 1
                    continue
                raise BinanceAPIError(
                    status_code=status_code, code=err_code, message=err_msg
                )

            if status_code >= 400:
                if (
                    status_code in self.RETRY_STATUS_CODES
                    and attempt < self._max_retries
                ):
                    time.sleep(0.5 * (2**attempt))
                    attempt += 1
                    continue
                msg = (
                    payload.get("msg", "Unknown HTTP error")
                    if isinstance(payload, dict)
                    else str(payload)
                )
                raise BinanceAPIError(status_code=status_code, code=None, message=msg)

            return payload

    def get_user_trades(
        self,
        symbol: str,
        start_time: int,
        end_time: int,
        limit: int = MAX_LIMIT,
    ) -> List[Dict[str, Any]]:
        payload = self._signed_get(
            "/fapi/v1/userTrades",
            {
                "symbol": symbol,
                "startTime": start_time,
                "endTime": end_time,
                "limit": min(limit, MAX_LIMIT),
            },
        )
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected userTrades response format.")
        return payload


def _iter_time_windows(start_ms: int, end_ms: int) -> Iterable[Tuple[int, int]]:
    cursor = start_ms
    while cursor <= end_ms:
        window_end = min(end_ms, cursor + MAX_WINDOW_MS)
        yield cursor, window_end
        cursor = window_end + 1


def fetch_trades_for_symbol(
    client: BinancePerpetualClient,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> List[Dict[str, Any]]:
    all_trades: List[Dict[str, Any]] = []
    seen_trade_ids = set()

    for window_start, window_end in _iter_time_windows(start_ms, end_ms):
        cursor = window_start
        pages = 0

        while cursor <= window_end:
            pages += 1
            if pages > 10000:
                raise RuntimeError("分頁次數過多，已中止以避免無限迴圈。")

            batch = client.get_user_trades(
                symbol=symbol,
                start_time=cursor,
                end_time=window_end,
                limit=MAX_LIMIT,
            )

            if not batch:
                break

            batch_sorted = sorted(
                batch,
                key=lambda item: (int(item.get("time", 0)), int(item.get("id", -1))),
            )

            max_time_in_batch = cursor
            for trade in batch_sorted:
                trade_id = int(trade.get("id", -1))
                if trade_id in seen_trade_ids:
                    continue
                seen_trade_ids.add(trade_id)
                all_trades.append(trade)
                trade_time = int(trade.get("time", 0))
                if trade_time > max_time_in_batch:
                    max_time_in_batch = trade_time

            if len(batch) < MAX_LIMIT:
                break

            next_cursor = max_time_in_batch + 1
            if next_cursor <= cursor:
                next_cursor = cursor + 1
            cursor = next_cursor

    return all_trades


def _guess_quote_asset(symbol: str) -> str:
    quote_candidates = [
        "USDT",
        "BUSD",
        "USDC",
        "FDUSD",
        "TUSD",
        "BTC",
        "ETH",
        "BNB",
    ]
    for quote in quote_candidates:
        if symbol.endswith(quote):
            return quote
    return ""


def aggregate_stats(symbol: str, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    trade_count = len(trades)

    volume_base = Decimal("0")
    volume_quote = Decimal("0")
    net_realized_pnl = Decimal("0")
    cumulative_loss = Decimal("0")

    fees_by_asset: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    quote_asset = _guess_quote_asset(symbol)
    fee_total_quote_asset = Decimal("0")

    for trade in trades:
        qty = _to_decimal(trade.get("qty"))
        price = _to_decimal(trade.get("price"))
        quote_qty = _to_decimal(trade.get("quoteQty"))

        volume_base += abs(qty)
        if quote_qty != 0:
            volume_quote += abs(quote_qty)
        else:
            volume_quote += abs(qty * price)

        commission = abs(_to_decimal(trade.get("commission")))
        commission_asset = str(trade.get("commissionAsset") or "UNKNOWN")
        fees_by_asset[commission_asset] += commission

        if commission_asset == quote_asset:
            fee_total_quote_asset += commission

        realized_pnl = _to_decimal(trade.get("realizedPnl"))
        net_realized_pnl += realized_pnl
        if realized_pnl < 0:
            cumulative_loss += -realized_pnl

    return {
        "symbol": symbol,
        "trade_count": trade_count,
        "volume_base": str(volume_base),
        "volume_quote": str(volume_quote),
        "fees_by_asset": {
            asset: str(value) for asset, value in sorted(fees_by_asset.items())
        },
        "fee_total_quote_asset": str(fee_total_quote_asset),
        "quote_asset": quote_asset,
        "cumulative_loss": str(cumulative_loss),
        "net_realized_pnl": str(net_realized_pnl),
    }


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def format_text_output(result: Dict[str, Any]) -> str:
    lines = [
        f"symbol: {result['symbol']}",
        f"start_time: {result['start_time_iso']}",
        f"end_time: {result['end_time_iso']}",
        f"trade_count: {result['trade_count']}",
        f"volume_base: {result['volume_base']}",
        f"volume_quote: {result['volume_quote']}",
        f"quote_asset: {result['quote_asset']}",
        f"fee_total_quote_asset: {result['fee_total_quote_asset']}",
        f"cumulative_loss: {result['cumulative_loss']}",
        f"net_realized_pnl: {result['net_realized_pnl']}",
        "fees_by_asset:",
    ]
    fees = result.get("fees_by_asset", {})
    for asset, value in fees.items():
        lines.append(f"  - {asset}: {value}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    try:
        start_ms, end_ms = resolve_time_range(
            args.start_time, args.end_time, args.lookback_days
        )
        symbol = FIXED_SYMBOL

        credentials_file = Path(args.env_file)
        if not credentials_file.is_absolute():
            credentials_file = credentials_file.resolve()

        api_key, api_secret = load_binance_api_credentials_from_file(
            credentials_file=credentials_file,
        )

        client = BinancePerpetualClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=args.base_url,
            recv_window=args.recv_window,
            timeout=args.timeout,
            max_retries=args.max_retries,
        )
        client.sync_server_time()

        trades = fetch_trades_for_symbol(
            client=client,
            symbol=symbol,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        stats = aggregate_stats(symbol=symbol, trades=trades)

        result = {
            **stats,
            "start_time_ms": start_ms,
            "end_time_ms": end_ms,
            "start_time_iso": _ms_to_iso(start_ms),
            "end_time_iso": _ms_to_iso(end_ms),
        }

        if args.output == "json":
            rendered = json.dumps(result, indent=2, ensure_ascii=False)
        else:
            rendered = format_text_output(result)

        print(rendered)

        if args.save:
            save_path = Path(args.save)
            save_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        return 0

    except (ConfigError, BinanceAPIError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
