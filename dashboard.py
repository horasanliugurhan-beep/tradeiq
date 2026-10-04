"""Local dashboard. Public prices + paper ledger + isolated testnet validation."""
import argparse
import csv
import io
import json
import secrets
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from simulator import Engine, load_events
from binance_adapter import AdapterError, PublicMarket, SYMBOLS, TestnetClient

ROOT = Path(__file__).parent
ISTANBUL = timezone(timedelta(hours=3))


class RuntimeLockError(ValueError):
    pass


class RuntimeLock:
    """OS lock held for process lifetime; released even after abnormal exit."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.mkdir(parents=True, exist_ok=True)
        stream = (self.path / ".instance.lock").open("a+b")
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"1")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise RuntimeLockError("Bu çalışma alanı zaten açık. Mevcut paneli kullanın veya farklı bir kayıt klasörü seçin.") from None
        self.file = stream
        return self

    def __exit__(self, *_):
        if self.file:
            self.file.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


class Application:
    def __init__(self, runtime, market=None, testnet_factory=TestnetClient):
        self.runtime = Path(runtime)
        self.market = market or PublicMarket()
        self.testnet_factory = testnet_factory
        self.lock = threading.RLock()
        self.network_lock = threading.Lock()
        self.generation = 0
        self.active = False
        self.automatic = False
        self.source = "demo"
        self.symbol = "SOL/USDT"
        selection = self.runtime / "selection.json"
        if selection.exists():
            saved = json.loads(selection.read_text(encoding="utf-8"))
            if saved.get("source") not in ("demo", "public") or saved.get("symbol") not in SYMBOLS:
                raise ValueError("Çalışma alanı seçimi bozuk. Kayıt sıfırlanmadı.")
            self.source, self.symbol = saved["source"], saved["symbol"]
        self.last_snapshot = None
        self.error = ""
        self.testnet = None
        self.testnet_status = None
        self.stop_event = threading.Event()
        self.demo_events, self.demo_digest = load_events(ROOT / "data/demo.csv")
        self.engine = self.load_engine()

    def load_engine(self):
        dataset = self.demo_digest if self.source == "demo" else "public-paper-v1:" + self.symbol
        name = "demo" if self.source == "demo" else "paper_" + self.symbol.replace("/", "")
        return Engine(state_path=self.runtime / (name + ".json"), dataset=dataset)

    def status(self):
        with self.lock:
            summary = self.engine.summary()
            summary["mode"] = "Kurgusal demo" if self.source == "demo" else "Gerçek fiyat • sanal işlem"
            current = self.engine.state
            quote = self.last_snapshot
            return dict(summary=summary, source=self.source, symbol=self.symbol, active=self.active,
                        automatic=self.automatic, error=self.error,
                        price=quote["price"] if quote else None,
                        price_age=round(time.time() - quote["fetched_at"]) if quote else None,
                        events=current["events"][-20:], fills=current["fills"][-20:],
                        chart=[{"equity": x["equity"], "time": x["timestamp"]} for x in current["events"][-200:]],
                        chart_truncated=len(current["events"]) > 200,
                        testnet=self.testnet_status, demo_total=len(self.demo_events))

    def apply_quote(self, quote, action="hold", prefix="manual"):
        if not 0 <= time.time() - quote["fetched_at"] <= 10:
            raise AdapterError("Fiyat eskidi. İşlem için fiyatı yenileyin.")
        event_id = ("candle-" + quote["candle_id"] if prefix == "auto" else
                    prefix + "-" + secrets.token_hex(8))
        if event_id in {x["id"] for x in self.engine.state["events"]}:
            action = "hold"
            event_id = "mark-" + secrets.token_hex(8)
        row = dict(id=event_id, timestamp=datetime.now(ISTANBUL).isoformat(),
                   symbol=self.symbol, price=quote["price"], action=action, reject="")
        self.engine.process(row)
        self.last_snapshot = quote
        self.error = ""

    def poll(self):
        with self.lock:
            if self.source != "public" or not (self.active or self.engine.state["positions"]):
                return
            generation, symbol = self.generation, self.symbol
        if not self.network_lock.acquire(blocking=False):
            return
        try:
            try:
                quote = self.market.snapshot(symbol)
                with self.lock:
                    if generation != self.generation:
                        return
                    action = quote["action"] if self.active and self.automatic else "hold"
                    self.apply_quote(quote, action, "auto" if self.automatic and self.active else "mark")
            except (AdapterError, ValueError, KeyError, TypeError, OSError, ArithmeticError):
                with self.lock:
                    if generation == self.generation:
                        self.error = "Güncel veri alınamadı. Yeni emir üretilmedi; pozisyonlar son bilinen fiyatla gösteriliyor."
        finally:
            self.network_lock.release()

    def network_command(self, data):
        if not self.network_lock.acquire(blocking=False):
            raise ValueError("Bir bağlantı isteği sürüyor. Sonucu bekleyin; duraklatma düğmesini kullanabilirsiniz.")
        try:
            action = data["command"]
            with self.lock:
                generation, symbol = self.generation, self.symbol
                if action in ("buy", "sell", "refresh") and self.source != "public":
                    raise ValueError("Önce gerçek fiyat kaynağını seçin.")
                if action == "testnet_validate":
                    client = self.testnet
                    if not client or not self.testnet_status["can_trade"]:
                        raise ValueError("İşlem izni olan test ortamına bağlanın.")
            if action in ("buy", "sell", "refresh"):
                quote = self.market.snapshot(symbol)
                with self.lock:
                    if generation != self.generation:
                        raise ValueError("Çalışma ayarı değişti; bekleyen işlem uygulanmadı.")
                    if action == "buy" and self.symbol in self.engine.state["positions"]:
                        raise ValueError("Bu paritede zaten açık sanal pozisyon var.")
                    if action == "sell" and self.symbol not in self.engine.state["positions"]:
                        raise ValueError("Satılacak açık sanal pozisyon yok.")
                    self.apply_quote(quote, action if action != "refresh" else "hold")
                    outcome = self.engine.state["events"][-1]["outcome"]
                return {"ok": True, "outcome": outcome}
            if action == "testnet_connect":
                client = self.testnet_factory(data.get("key"), data.get("secret"))
                try:
                    account = client.account()
                    with self.lock:
                        if generation != self.generation:
                            raise ValueError("Bağlantı iptal edildi; yeniden deneyin.")
                        if self.testnet:
                            self.testnet.close()
                        self.testnet, self.testnet_status = client, account
                    return {"ok": True}
                except Exception:
                    client.close()
                    raise
            result = client.validate_buy(symbol, data.get("amount", "10"))
            with self.lock:
                if self.testnet is not client:
                    raise ValueError("Test bağlantısı kapatıldı.")
            return result
        finally:
            self.network_lock.release()

    def command(self, data):
        allowed = {"configure", "step", "demo_restart", "start", "pause", "buy", "sell", "refresh", "testnet_connect", "testnet_validate", "testnet_disconnect"}
        if not isinstance(data, dict) or not isinstance(data.get("command"), str) or data["command"] not in allowed:
            raise ValueError("Desteklenmeyen işlem.")
        if "automatic" in data and type(data["automatic"]) is not bool:
            raise ValueError("Otomatik işlem seçimi geçersiz.")
        if data["command"] == "configure" and (not isinstance(data.get("source"), str) or not isinstance(data.get("symbol"), str)):
            raise ValueError("Geçersiz veri kaynağı veya parite.")
        if data.get("command") in ("buy", "sell", "refresh", "testnet_connect", "testnet_validate"):
            return self.network_command(data)
        with self.lock:
            action = data.get("command")
            if action == "configure":
                if self.active or self.engine.state["positions"]:
                    raise ValueError("Önce duraklatın ve mevcut sanal pozisyonları kapatın.")
                source, symbol = data.get("source"), data.get("symbol")
                if source not in ("demo", "public") or symbol not in SYMBOLS:
                    raise ValueError("Geçersiz veri kaynağı veya parite.")
                previous = (self.source, self.symbol, self.engine)
                self.source, self.symbol = source, symbol
                try:
                    self.engine = self.load_engine()
                    self.runtime.mkdir(parents=True, exist_ok=True)
                    pending = self.runtime / "selection.json.tmp"
                    pending.write_text(json.dumps({"source": source, "symbol": symbol}), encoding="utf-8")
                    pending.replace(self.runtime / "selection.json")
                except Exception:
                    self.source, self.symbol, self.engine = previous
                    raise
                self.last_snapshot = None
                self.error = ""
                self.automatic = False
                self.generation += 1
            elif action == "step":
                if self.source != "demo":
                    raise ValueError("Bu düğme kurgusal demo içindir.")
                index = self.engine.state["cursor"]
                if index >= len(self.demo_events):
                    raise ValueError("Demo tamamlandı. Yeni demo için ayrı bir çalışma klasörü kullanın.")
                self.engine.process(self.demo_events[index])
            elif action == "demo_restart":
                if self.source != "demo" or self.active:
                    raise ValueError("Yalnızca kurgusal demo yeniden oynatılabilir.")
                if self.engine.path.exists():
                    archive = self.runtime / "demo_archive"
                    archive.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(self.engine.path, archive / ("demo-" + secrets.token_hex(8) + ".json"))
                fresh = Engine(state_path=None, dataset=self.demo_digest)
                fresh.path = self.engine.path
                fresh.save()
                self.engine = fresh
                self.generation += 1
            elif action == "start":
                if self.source != "public":
                    raise ValueError("Gerçek fiyat kaynağını seçin veya demoyu adım adım oynatın.")
                self.active = True
                self.automatic = data.get("automatic") is True
                self.generation += 1
            elif action == "pause":
                self.active = self.automatic = False
                self.generation += 1
            elif action == "testnet_disconnect":
                if self.testnet:
                    self.testnet.close()
                self.testnet = self.testnet_status = None
                self.generation += 1
            else:
                raise ValueError("Desteklenmeyen işlem.")
            return {"ok": True}

    def worker(self):
        while not self.stop_event.wait(15):
            self.poll()


def make_handler(app, token):
    class Handler(BaseHTTPRequestHandler):
        timeout = 10
        server_version = "TradeIQ"
        sys_version = ""
        def log_message(self, *_):
            pass  # no request/credential logs

        def valid_host(self):
            port = self.server.server_address[1]
            return len(self.headers.get_all("Host", [])) == 1 and self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def authorized(self):
            port = self.server.server_address[1]
            origin = self.headers.get("Origin")
            supplied = self.headers.get("X-App-Token", "")
            return (self.valid_host() and supplied.isascii() and len(supplied) < 128
                    and len(self.headers.get_all("X-App-Token", [])) == 1
                    and secrets.compare_digest(supplied, token)
                    and self.headers.get("Sec-Fetch-Site") not in ("cross-site",)
                    and origin in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"))

        def send(self, code, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {"error": "Geçersiz sunucu adresi."})
            if self.path == "/":
                html = (ROOT / "ui/index.html").read_text(encoding="utf-8").replace("__APP_TOKEN__", token)
                return self.send(200, html, "text/html; charset=utf-8")
            if self.path in ("/app.js", "/style.css"):
                file = ROOT / "ui" / self.path[1:]
                return self.send(200, file.read_bytes(), "text/javascript; charset=utf-8" if file.suffix == ".js" else "text/css; charset=utf-8")
            if not self.authorized():
                return self.send(403, {"error": "Oturum doğrulanamadı. Paneli yenileyin."})
            if self.path == "/api/state":
                return self.send(200, app.status())
            if self.path == "/api/export":
                with app.lock:
                    rows = app.engine.state["events"]
                    stream = io.StringIO(newline="")
                    if rows:
                        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                        writer.writeheader()
                        writer.writerows({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v
                                          for k, v in row.items()} for row in rows)
                return self.send(200, stream.getvalue(), "text/csv; charset=utf-8")
            self.send(404, {"error": "Sayfa bulunamadı."})

        def do_POST(self):
            if self.path != "/api/command" or not self.authorized():
                return self.send(403, {"error": "İstek doğrulanamadı."})
            try:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("JSON gerekli.")
                if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                    raise ValueError("Geçersiz istek çerçevesi.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 8192:
                    raise ValueError("Geçersiz istek boyutu.")
                self.connection.settimeout(10)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Eksik istek.")
                def strict_object(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("Tekrarlı JSON alanı.")
                        result[key] = value
                    return result
                data = json.loads(raw, object_pairs_hook=strict_object)
                if not isinstance(data, dict):
                    raise ValueError("Geçersiz istek.")
                result = app.command(data)
                self.send(200, result)
            except (AdapterError, ValueError) as exc:
                self.send(400, {"error": str(exc)})
            except Exception:
                self.send(503, {"error": "İşlem tamamlanamadı. Bağlantı ve kayıt dosyalarını kontrol edin."})
    return Handler


def main():
    parser = argparse.ArgumentParser(description="TradeIQ local prototype")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime")
    args = parser.parse_args()
    try:
        with RuntimeLock(args.runtime):
            serve(args)
    except RuntimeLockError as exc:
        parser.exit(2, str(exc) + "\n")
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, "Panel açılamadı. Portun boş olduğunu ve kayıt dosyalarının sağlam olduğunu kontrol edin; kayıtlar otomatik silinmedi.\n")


def serve(args):
    app = Application(args.runtime)
    server = LocalServer(("127.0.0.1", args.port), make_handler(app, secrets.token_urlsafe(32)))
    thread = threading.Thread(target=app.worker, daemon=True)
    thread.start()
    print(f"TradeIQ panel: http://127.0.0.1:{server.server_address[1]} — yalnızca sanal işlem", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_event.set()
        with app.lock:
            if app.testnet:
                app.testnet.close()
        server.server_close()


class LocalServer(ThreadingHTTPServer):
    """Bound request concurrency to limit local resource exhaustion."""
    daemon_threads = True
    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


if __name__ == "__main__":
    main()
