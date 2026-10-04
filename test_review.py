import copy
import http.client
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from binance_adapter import AdapterError, PublicMarket, TestnetClient
from dashboard import Application, LocalServer, RuntimeLock, make_handler
from simulator import Engine, number
from test_dashboard import FakeMarket, FakeTransport


class ReviewApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.market = FakeMarket()
        self.app = Application(self.temp.name, self.market)
        self.app.command(dict(command='configure', source='public', symbol='SOL/USDT'))

    def tearDown(self):
        self.temp.cleanup()

    def pending_request(self, action):
        entered, release = threading.Event(), threading.Event()
        original = self.market.snapshot
        def blocked(symbol):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Test did not release network request')
            return original(symbol)
        self.market.snapshot = blocked
        errors = []
        def run():
            try:
                self.app.command(dict(command=action))
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(entered.wait(1))
        return thread, release, errors

    def test_pause_and_status_remain_responsive_during_network(self):
        self.app.command(dict(command='start', automatic=True))
        thread, release, errors = self.pending_request('buy')
        try:
            start = time.monotonic()
            self.app.command(dict(command='pause'))
            state = self.app.status()
            self.assertLess(time.monotonic() - start, .5)
            self.assertFalse(state['active'])
        finally:
            release.set(); thread.join(2)
        self.assertFalse(self.app.engine.state['positions'])
        self.assertEqual(len(errors), 1)

    def test_late_price_does_not_trade_wrong_symbol(self):
        thread, release, errors = self.pending_request('buy')
        try:
            self.app.command(dict(command='configure',source='public',symbol='BTC/USDT'))
        finally:
            release.set(); thread.join(2)
        self.assertFalse(self.app.engine.state['fills'])
        self.assertEqual(self.app.symbol, 'BTC/USDT')
        self.assertEqual(len(errors), 1)

    def test_second_network_request_is_rejected_without_blocking(self):
        thread, release, _ = self.pending_request('refresh')
        try:
            with self.assertRaises(ValueError):self.app.command(dict(command='buy'))
        finally:
            release.set(); thread.join(2)

    def test_pause_during_automatic_fetch_prevents_late_buy(self):
        entered, release = threading.Event(), threading.Event()
        original = self.market.snapshot
        def blocked(symbol):
            entered.set();release.wait(2)
            result = original(symbol);result['action']='buy';return result
        self.market.snapshot=blocked
        self.app.command(dict(command='start',automatic=True))
        thread=threading.Thread(target=self.app.poll);thread.start()
        self.assertTrue(entered.wait(1))
        self.app.command(dict(command='pause'));release.set();thread.join(2)
        self.assertFalse(self.app.engine.state['fills'])

    def test_sell_without_position_and_second_buy_are_clear_errors(self):
        with self.assertRaises(ValueError):self.app.command(dict(command='sell'))
        self.app.command(dict(command='buy'))
        with self.assertRaises(ValueError):self.app.command(dict(command='buy'))
        self.assertEqual(len(self.app.engine.state['fills']),1)

    def test_future_price_is_rejected(self):
        quote=self.market.snapshot('SOL/USDT');quote['fetched_at']+=60
        with self.assertRaises(AdapterError):self.app.apply_quote(quote,'buy')
        self.assertFalse(self.app.engine.state['fills'])

    def test_invalid_command_types_do_not_mutate_state(self):
        before=copy.deepcopy(self.app.engine.state)
        for data in [[],{'command':[]},{'command':'start','automatic':'false'},
                     {'command':'configure','source':{},'symbol':[]}]:
            with self.assertRaises(ValueError):self.app.command(data)
        self.assertEqual(before,self.app.engine.state)

    def test_demo_replay_preserves_old_run_in_archive(self):
        self.app.command(dict(command='configure',source='demo',symbol='SOL/USDT'))
        self.app.command(dict(command='step'));self.app.command(dict(command='step'))
        before=copy.deepcopy(self.app.engine.state)
        self.app.command(dict(command='demo_restart'))
        self.assertEqual(self.app.engine.state['cursor'],0)
        files=list((Path(self.temp.name)/'demo_archive').glob('*.json'))
        self.assertEqual(json.loads(files[0].read_text()),before)
        self.app.command(dict(command='step'))
        self.assertEqual(self.app.engine.state['cursor'],1)

    def test_testnet_without_trading_permission_cannot_validate(self):
        client=TestnetClient('K'*32,'S'*32,FakeTransport())
        self.app.testnet=client;self.app.testnet_status=dict(can_trade=False,balances=[])
        with self.assertRaises(ValueError):self.app.command(dict(command='testnet_validate',amount='10'))
        self.assertEqual(client.transport.calls,[])


class ReviewInputTests(unittest.TestCase):
    def test_numeric_resource_exhaustion_inputs_are_rejected(self):
        for value in ['1e999999999','1e-999999999','9'*1000,'0E99999','bad-number','Infinity','NaN']:
            with self.subTest(value=value[:30]),self.assertRaises(ValueError):number(value)

    def test_malformed_exchange_data_fails_closed(self):
        for payload in [None, [], {}, {'serverTime':'secret-invalid-time'}]:
            transport=FakeTransport()
            transport.request=lambda *args,**kwargs:payload
            with self.assertRaises(AdapterError) as error:PublicMarket(transport).snapshot('SOL/USDT')
            self.assertNotIn('secret-invalid-time',str(error.exception))

    def test_checkpoint_duplicate_ids_and_invalid_drawdown_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(tmp,FakeMarket());app.command(dict(command='step'));app.command(dict(command='step'))
            original=copy.deepcopy(app.engine.state)
            for changed in ('ids','drawdown'):
                data=copy.deepcopy(original)
                if changed=='ids':data['events'][1]['id']=data['events'][0]['id']
                else:data['max_drawdown']='NaN'
                app.engine.path.write_text(json.dumps(data),encoding='utf-8')
                with self.assertRaises(ValueError):Engine(state_path=app.engine.path,dataset=app.demo_digest)

    def test_runtime_lock_prevents_two_writers_and_can_be_reopened(self):
        with tempfile.TemporaryDirectory() as tmp:
            with RuntimeLock(tmp):
                with self.assertRaises(ValueError):
                    with RuntimeLock(tmp):pass
            with RuntimeLock(tmp):pass

    def test_closed_testnet_client_cannot_sign(self):
        client=TestnetClient('K'*32,'S'*32,FakeTransport());client.close()
        with self.assertRaises(AdapterError):client.account()

    def test_second_panel_process_exits_with_clear_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            with RuntimeLock(tmp):
                result = subprocess.run([sys.executable, str(Path(__file__).parent / 'dashboard.py'),
                                         '--runtime', tmp, '--port', '0'], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(b'Traceback', result.stderr)
            self.assertGreater(len(result.stderr), 10)


class ReviewHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Application(self.temp.name,FakeMarket())
        self.server=LocalServer(('127.0.0.1',0),make_handler(self.app,'test-token'))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.port=self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def post(self,body,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2)
        request_headers={'X-App-Token':'test-token','Content-Type':'application/json'}
        request_headers.update(headers or {})
        connection.request('POST','/api/command',body=body,headers=request_headers)
        response=connection.getresponse();payload=response.read();status=response.status
        connection.close();return status,payload

    def test_invalid_json_and_duplicate_fields_are_rejected(self):
        for body in ['{bad','[]','{"command":[],"automatic":false}',
                     '{"command":"step","command":"demo_restart"}']:
            status,_=self.post(body)
            self.assertEqual(status,400)
        self.assertEqual(self.app.engine.state['cursor'],0)

    def test_oversized_body_and_transfer_encoding_rejected(self):
        for body,headers in [('x'*9000,{}), ('{}',{'Transfer-Encoding':'chunked'})]:
            self.assertEqual(self.post(body,headers)[0],400)

    def test_non_ascii_token_and_cross_site_request_do_not_crash(self):
        self.assertEqual(self.post('{}',{'X-App-Token':'é'})[0],403)
        self.assertEqual(self.post('{}',{'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.post('{"command":"step"}')[0],200)

    def test_sensitive_files_are_not_served(self):
        for path in ['/../simulator.py','/runtime/demo.json','/reference_source/core/config.py.txt','/.env']:
            connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2)
            connection.request('GET',path,headers={'X-App-Token':'test-token'})
            response=connection.getresponse();self.assertEqual(response.status,404);response.read();connection.close()

    def test_server_hides_python_version_and_sets_permissions_policy(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2);connection.request('GET','/')
        response=connection.getresponse()
        self.assertNotIn('Python',response.getheader('Server'))
        self.assertIn('camera=()',response.getheader('Permissions-Policy'))
        response.read();connection.close()

    def test_thread_slots_are_bounded(self):
        acquired=[self.server.slots.acquire(blocking=False) for _ in range(16)]
        self.assertTrue(all(acquired));self.assertFalse(self.server.slots.acquire(blocking=False))
        for _ in acquired:self.server.slots.release()


if __name__=='__main__':unittest.main(verbosity=2)
