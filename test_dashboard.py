import hashlib
import hmac
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

from binance_adapter import AdapterError, NoRedirect, PUBLIC_BASE, TEST_BASE, PublicMarket, TestnetClient, Transport
from dashboard import Application, make_handler


class FakeTransport:
    def __init__(self):
        self.calls = []

    def request(self, base, path, method="GET", params=None, key=None):
        self.calls.append((base, path, method, params, key))
        if path.endswith('/time'):
            return {'serverTime': int(time.time() * 1000)}
        if path.endswith('/account'):
            return {'canTrade': True, 'balances': [{'asset': 'USDT', 'free': '1000'}]}
        if path.endswith('/ticker/price'):
            return {'symbol': params['symbol'], 'price': '100'}
        if path.endswith('/klines'):
            now = int(time.time() * 1000)
            boundary = now // 60000 * 60000
            return [[boundary - (40-i)*60000,'100','101','99','100','1',boundary-(39-i)*60000-1]
                    for i in range(40)]
        if path.endswith('/order/test'):
            return {}
        raise AssertionError('Unexpected request')


class FakeMarket:
    def __init__(self):
        self.price = '100'
        self.action = 'hold'
        self.candle_id = '1'

    def snapshot(self, symbol):
        return dict(price=self.price, action=self.action, candle_id=self.candle_id,
                    fetched_at=time.time(), server_ms=int(time.time()*1000))


class AdapterTests(unittest.TestCase):
    def test_only_allowlisted_hosts_and_paths_can_be_called(self):
        t = Transport()
        for base, path, method in [('https://api.binance.com','/api/v3/order','POST'),
                                  (TEST_BASE,'/api/v3/order','POST'),
                                  (TEST_BASE,'/sapi/v1/capital/withdraw/apply','POST'),
                                  ('https://evil.example','/api/v3/account','GET')]:
            with self.subTest(path=path), self.assertRaises(AdapterError):
                t.request(base,path,method)

    def test_secret_cannot_be_sent_to_public_host(self):
        with self.assertRaises(AdapterError):
            Transport().request(PUBLIC_BASE,'/api/v3/time',key='not-a-real-key')

    def test_redirects_are_refused(self):
        with self.assertRaises(AdapterError):
            NoRedirect().redirect_request(None,None,302,'',{},'https://evil.example')

    def test_hmac_and_test_endpoint_without_matching_order(self):
        transport = FakeTransport()
        client = TestnetClient('K'*32,'S'*32,transport)
        result = client.validate_buy('SOL/USDT','10')
        base,path,method,params,key = transport.calls[-1]
        self.assertEqual((base,path,method), (TEST_BASE,'/api/v3/order/test','POST'))
        unsigned = {k:v for k,v in params.items() if k!='signature'}
        expected = hmac.new(('S'*32).encode(),urlencode(unsigned).encode(),hashlib.sha256).hexdigest()
        self.assertEqual(params['signature'],expected)
        self.assertFalse(result['matched_order'])
        self.assertEqual(key,'K'*32)
        client.close()
        self.assertEqual(client.secret,'')

    def test_amount_limits_and_symbols(self):
        client=TestnetClient('K'*32,'S'*32,FakeTransport())
        for amount in ['0','4','101','NaN','Infinity']:
            with self.subTest(amount=amount),self.assertRaises(AdapterError):
                client.validate_buy('SOL/USDT',amount)
        with self.assertRaises(AdapterError):
            client.validate_buy('EVIL/USDT','10')

    def test_public_uses_only_closed_candles(self):
        transport=FakeTransport()
        quote=PublicMarket(transport).snapshot('SOL/USDT')
        self.assertEqual(quote['action'],'hold')
        self.assertEqual(quote['price'],'100')
        self.assertTrue(all(call[0]==PUBLIC_BASE and call[4] is None for call in transport.calls))

    def test_clock_drift_and_old_candles_fail_closed(self):
        t=FakeTransport()
        original=t.request
        def old_time(base,path,*args,**kwargs):
            if path.endswith('/time'):return {'serverTime':0}
            return original(base,path,*args,**kwargs)
        t.request=old_time
        with self.assertRaises(AdapterError):PublicMarket(t).snapshot('SOL/USDT')
        t=FakeTransport();original=t.request
        def old_candles(base,path,*args,**kwargs):
            response=original(base,path,*args,**kwargs)
            if path.endswith('/klines'):
                for c in response:c[0]-=600000;c[6]-=600000
            return response
        t.request=old_candles
        with self.assertRaises(AdapterError):PublicMarket(t).snapshot('SOL/USDT')

    def test_rate_limit_backoff_prevents_requests(self):
        t=Transport();t.blocked_until=time.monotonic()+60
        with self.assertRaises(AdapterError):t.request(PUBLIC_BASE,'/api/v3/time')


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.market=FakeMarket()
        self.app=Application(self.temp.name, self.market,
                             lambda key,secret:TestnetClient(key,secret,FakeTransport()))

    def tearDown(self):self.temp.cleanup()

    def public(self):self.app.command(dict(command='configure',source='public',symbol='SOL/USDT'))

    def test_demo_survives_restart(self):
        self.app.command({'command':'step'})
        self.app.command({'command':'step'})
        restored=Application(self.temp.name,self.market)
        self.assertEqual(restored.engine.state,self.app.engine.state)
        self.assertFalse(restored.active)

    def test_manual_public_trade_and_restart(self):
        self.public();self.app.command({'command':'buy'})
        restored=Application(self.temp.name,self.market)
        self.assertEqual(restored.source,'public')
        self.assertEqual(restored.engine.state,self.app.engine.state)
        self.assertFalse(restored.active)
        self.market.price='103';restored.command({'command':'sell'})
        self.assertFalse(restored.engine.state['positions'])

    def test_pause_keeps_stop_monitoring(self):
        self.public();self.app.command({'command':'buy'})
        self.app.command({'command':'pause'})
        self.market.price='80';self.app.poll()
        self.assertFalse(self.app.engine.state['positions'])
        self.assertEqual(self.app.engine.state['fills'][-1]['reason'],'stop')

    def test_failed_price_fetch_does_not_change_ledger(self):
        self.public();before=json.dumps(self.app.engine.state)
        with patch.object(self.market,'snapshot',side_effect=AdapterError('timeout')):
            with self.assertRaises(AdapterError):self.app.command({'command':'buy'})
        self.assertEqual(json.dumps(self.app.engine.state),before)

    def test_stale_price_cannot_trade(self):
        self.public();quote=self.market.snapshot('SOL/USDT');quote['fetched_at']-=30
        with self.assertRaises(AdapterError):self.app.apply_quote(quote,'buy')
        self.assertEqual(self.app.engine.state['cursor'],0)

    def test_one_auto_signal_per_closed_candle(self):
        self.public();self.market.action='buy'
        self.app.command(dict(command='start',automatic=True));self.app.poll();self.app.poll()
        self.assertEqual(len(self.app.engine.state['fills']),1)
        self.market.action='sell';self.app.poll()
        self.assertEqual(len(self.app.engine.state['fills']),1)
        self.market.candle_id='2';self.app.poll()
        self.assertEqual(len(self.app.engine.state['fills']),2)

    def test_source_cannot_change_with_open_position(self):
        self.public();self.app.command({'command':'buy'})
        with self.assertRaises(ValueError):self.app.command(dict(command='configure',source='demo',symbol='SOL/USDT'))

    def test_keys_are_not_exposed_or_persisted(self):
        self.app.command(dict(command='testnet_connect',key='K'*32,secret='S'*32))
        self.app.command({'command':'step'})
        combined=json.dumps(self.app.status())+''.join(p.read_text() for p in Path(self.temp.name).glob('*.json'))
        self.assertNotIn('K'*32,combined);self.assertNotIn('S'*32,combined)
        response=self.app.command(dict(command='testnet_validate',amount='10'))
        self.assertTrue(response['validated'])
        self.app.command({'command':'testnet_disconnect'})
        self.assertIsNone(self.app.testnet)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Application(self.temp.name,FakeMarket())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.app,'test-token'))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def test_dashboard_and_token_authorization(self):
        with urlopen(self.url) as response:
            self.assertIn(b'TradeIQ',response.read())
            self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        with self.assertRaises(HTTPError):urlopen(self.url+'/api/state')
        with urlopen(Request(self.url+'/api/state',headers={'X-App-Token':'test-token'})) as response:
            self.assertEqual(json.load(response)['source'],'demo')

    def test_cross_origin_and_rebinding_are_rejected(self):
        for headers in [{'Host':'evil.example'}, {'X-App-Token':'test-token','Origin':'https://evil.example'}]:
            with self.assertRaises(HTTPError):urlopen(Request(self.url+'/api/state',headers=headers))

    def test_demo_command_and_export(self):
        request=Request(self.url+'/api/command',data=b'{"command":"step"}',headers={'X-App-Token':'test-token','Content-Type':'application/json'},method='POST')
        with urlopen(request) as response:self.assertTrue(json.load(response)['ok'])
        with urlopen(Request(self.url+'/api/export',headers={'X-App-Token':'test-token'})) as response:
            self.assertIn(b'buy_rejected',response.read())


if __name__=='__main__':unittest.main(verbosity=2)
