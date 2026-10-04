"""Allowlisted public prices and Spot Testnet validation; no production orders."""
import hashlib
import hmac
import json
import time
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from simulator import number

SYMBOLS = {"SOL/USDT", "BTC/USDT", "ETH/USDT"}
PUBLIC_BASE = "https://data-api.binance.vision"
TEST_BASE = "https://testnet.binance.vision"


class AdapterError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AdapterError("Yönlendirme reddedildi; bağlantı adresi değiştirilmedi.")


class Transport:
    def __init__(self):
        self.opener = build_opener(NoRedirect())
        self.blocked_until = 0

    def request(self, base, path, method="GET", params=None, key=None):
        allowed = {
            (PUBLIC_BASE, "/api/v3/time", "GET"),
            (PUBLIC_BASE, "/api/v3/ticker/price", "GET"),
            (PUBLIC_BASE, "/api/v3/klines", "GET"),
            (TEST_BASE, "/api/v3/time", "GET"),
            (TEST_BASE, "/api/v3/account", "GET"),
            (TEST_BASE, "/api/v3/order/test", "POST"),
        }
        if (base, path, method) not in allowed:
            raise AdapterError("Bu bağlantı veya işlem desteklenmiyor.")
        if time.monotonic() < self.blocked_until:
            raise AdapterError("Borsa istek sınırı nedeniyle bağlantı bekletiliyor.")
        if key and base != TEST_BASE:
            raise AdapterError("Anahtar yalnızca test ortamına gönderilebilir.")
        query = urlencode(params or {})
        url = base + path + ("?" + query if query and method == "GET" else "")
        headers = {"Accept": "application/json", "Cache-Control": "no-cache", "User-Agent": "TradeIQ-Local-Prototype/0.3"}
        if key:
            headers["X-MBX-APIKEY"] = key
        data = query.encode() if method == "POST" else None
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            with self.opener.open(Request(url, data=data, headers=headers, method=method), timeout=8) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise AdapterError("Borsa yanıtı beklenenden büyük; işlem uygulanmadı.")
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code in (418, 429):
                try:
                    wait = max(60, min(3600, int(exc.headers.get("Retry-After", "60"))))
                except ValueError:
                    wait = 60
                self.blocked_until = time.monotonic() + wait
            # Never return response bodies, signed URLs or credentials to UI/logs.
            raise AdapterError(f"Borsa isteği kabul etmedi (HTTP {exc.code}). Anahtar ve izinleri kontrol edin.") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise AdapterError("Borsaya ulaşılamadı. Yeni işlem yapılmadı; yeniden deneyin.") from None


def exchange_symbol(symbol):
    if symbol not in SYMBOLS:
        raise AdapterError("Desteklenmeyen parite.")
    return symbol.replace("/", "")


def ema(values, period):
    result = []
    current = values[0]
    alpha = Decimal(2) / (period + 1)
    for value in values:
        current = alpha * value + (1 - alpha) * current
        result.append(current)
    return result


class PublicMarket:
    def __init__(self, transport=None):
        self.transport = transport or Transport()

    def snapshot(self, symbol):
        try:
            return self._snapshot(symbol)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, ArithmeticError):
            raise AdapterError("Borsa verisi doğrulanamadı; işlem uygulanmadı.") from None

    def _snapshot(self, symbol):
        symbol_code = exchange_symbol(symbol)
        start = time.monotonic()
        server_ms = int(self.transport.request(PUBLIC_BASE, "/api/v3/time")["serverTime"])
        if abs(server_ms - int(time.time() * 1000)) > 30_000:
            raise AdapterError("Bilgisayar saati ile borsa saati uyuşmuyor; işlem durduruldu.")
        quote = self.transport.request(PUBLIC_BASE, "/api/v3/ticker/price", params={"symbol": symbol_code})
        if quote.get("symbol") != symbol_code:
            raise AdapterError("Fiyat yanıtı pariteyle uyuşmuyor.")
        price = number(quote["price"], True)
        candles = self.transport.request(PUBLIC_BASE, "/api/v3/klines",
                    params={"symbol": symbol_code, "interval": "1m", "limit": 100})
        closed = [c for c in candles if int(c[6]) < server_ms]
        if len(closed) < 30 or server_ms - int(closed[-1][6]) > 120_000:
            raise AdapterError("Güncel kapanmış mum verisi yok; işlem durduruldu.")
        if any(int(a[0]) >= int(b[0]) or int(b[0]) - int(a[0]) != 60_000
               for a, b in zip(closed, closed[1:])):
            raise AdapterError("Mum verisinde boşluk veya sıralama hatası var.")
        if time.monotonic() - start > 10:
            raise AdapterError("Veri geç geldi; bu fiyatla işlem yapılmadı.")
        closes = [number(c[4], True) for c in closed]
        fast, slow = ema(closes, 9), ema(closes, 21)
        action = ("buy" if fast[-2] <= slow[-2] and fast[-1] > slow[-1] else
                  "sell" if fast[-2] >= slow[-2] and fast[-1] < slow[-1] else "hold")
        return dict(price=str(price), action=action, candle_id=str(closed[-1][6]),
                    server_ms=server_ms, fetched_at=time.time())


class TestnetClient:
    """HMAC test keys in memory only; validates orders without matching them."""
    def __init__(self, key, secret, transport=None):
        if not isinstance(key, str) or not isinstance(secret, str) or not (16 <= len(key) <= 256 and 16 <= len(secret) <= 256):
            raise AdapterError("Geçerli test ortamı anahtarlarını girin.")
        if not key.isascii() or not secret.isascii() or any(c.isspace() for c in key + secret):
            raise AdapterError("Anahtar biçimi geçersiz.")
        self.key, self.secret = key, secret
        self.transport = transport or Transport()

    def signed(self, path, method="GET", params=None):
        if not self.key or not self.secret:
            raise AdapterError("Test bağlantısı kapalı.")
        stamp = int(self.transport.request(TEST_BASE, "/api/v3/time")["serverTime"])
        values = dict(params or {}, recvWindow=5000, timestamp=stamp)
        values["signature"] = hmac.new(self.secret.encode(), urlencode(values).encode(), hashlib.sha256).hexdigest()
        return self.transport.request(TEST_BASE, path, method, values, self.key)

    def account(self):
        data = self.signed("/api/v3/account")
        if not isinstance(data, dict) or type(data.get("canTrade")) is not bool or not isinstance(data.get("balances"), list):
            raise AdapterError("Test hesabı yanıtı doğrulanamadı.")
        balances = [{"asset": x["asset"], "free": str(number(x["free"]))}
                    for x in data.get("balances", []) if x["asset"] in ("USDT", "SOL", "BTC", "ETH")]
        return dict(can_trade=bool(data.get("canTrade")), balances=balances)

    def validate_buy(self, symbol, amount):
        try:
            amount = number(amount, True)
        except (ValueError, ArithmeticError):
            raise AdapterError("Test tutarı 5–100 USDT arasında olmalı.") from None
        if amount < 5 or amount > 100:
            raise AdapterError("Test tutarı 5–100 USDT arasında olmalı.")
        result = self.signed("/api/v3/order/test", "POST", {
            "symbol": exchange_symbol(symbol), "side": "BUY", "type": "MARKET",
            "quoteOrderQty": format(amount, "f")})
        if not isinstance(result, dict) or "code" in result:
            raise AdapterError("Test doğrulaması tamamlanamadı.")
        return {"validated": True, "matched_order": False,
                "message": "Test emri doğrulandı. Alım gerçekleşmedi; bakiye değişmedi."}

    def close(self):
        self.key = self.secret = ""
