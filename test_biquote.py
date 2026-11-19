import copy
import json
import unittest
import pandas as pd

from biquote import fetch_biquote
from fx import indicators


class Response:
    def __init__(self,payload,status=200):
        self.status_code=status
        self.body=json.dumps(payload).encode()
        self.closed=False
    def iter_content(self,chunk_size):
        yield self.body
    def close(self): self.closed=True


def fixture(pair='USD/JPY',label='5m'):
    minutes={'5m':5,'15m':15,'4h':240}[label]
    stamps=pd.date_range('2026-11-01',periods=80,freq=f'{minutes}min',tz='UTC')
    bars=[dict(openTime=stamp.isoformat(),open=100.,high=101.,low=99.,close=100.,isOpen=False) for stamp in stamps]
    now=stamps[-1]+pd.Timedelta(minutes=minutes)
    return dict(symbol=pair.replace('/',''),interval=label,bars=list(reversed(bars))),now


class BiquoteTests(unittest.TestCase):
    def test_live_response_across_boundary_preserves_report_cutoff(self):
        payload, now = fixture()
        step = pd.Timedelta(minutes=5)
        open_bar = dict(payload['bars'][0], openTime=(now + step).isoformat(), isOpen=True)
        later_bar = dict(payload['bars'][0], openTime=now.isoformat())
        payload['bars'][:0] = [open_bar, later_bar]
        times = iter([now + step, now + step + pd.Timedelta(seconds=1)])
        result = fetch_biquote('USD/JPY', '5m', now, get=lambda *a, **k: Response(payload),
                               clock=lambda: next(times))
        self.assertEqual(len(result), 80)
        self.assertEqual(result.index[-1] + step, now)
        times = iter([now, now])
        with self.assertRaisesRegex(ValueError, 'BIQUOTE_TIMESTAMP_INVALID'):
            fetch_biquote('USD/JPY', '5m', now, get=lambda *a, **k: Response(payload),
                          clock=lambda: next(times))

    def fetch(self,payload,now,pair='USD/JPY',label='5m'):
        return fetch_biquote(pair,label,now,get=lambda *args,**kwargs:Response(payload))

    def test_provider_prepends_open_bar_to_full_closed_response(self):
        payload, now = fixture()
        first = pd.Timestamp(payload['bars'][-1]['openTime'])
        for offset in range(1, 21):
            older = dict(payload['bars'][-1], openTime=(first-pd.Timedelta(minutes=5 * offset)).isoformat())
            payload['bars'].append(older)
        payload['bars'].insert(0, dict(payload['bars'][0], openTime=now.isoformat(), isOpen=True))
        self.assertEqual(len(self.fetch(payload, now)), 100)
        payload['bars'][0]['openTime'] = (first-pd.Timedelta(minutes=105)).isoformat()
        payload['bars'][0]['isOpen'] = False
        payload['bars'] = payload['bars'][1:] + payload['bars'][:1]
        with self.assertRaisesRegex(ValueError, 'BIQUOTE_BARS_INVALID'):
            self.fetch(payload, now)

    def test_all_pair_native_frames_closed_and_bounded_request(self):
        for pair in ('USD/JPY','MXN/JPY','AUD/USD','EUR/USD','AUD/JPY'):
            for label in ('5m','15m','4h'):
                payload,now=fixture(pair,label); calls=[]; response=Response(payload)
                def get(*args,**kwargs): calls.append((args,kwargs)); return response
                result=fetch_biquote(pair,label,now,get=get)
                self.assertEqual(len(result),80)
                self.assertTrue(result.index.is_monotonic_increasing)
                self.assertTrue(response.closed)
                self.assertEqual(len(calls),1)
                self.assertEqual(calls[0][1]['params'],{'interval':label,'limit':100})
                self.assertIn('timeout',calls[0][1])
                self.assertFalse(calls[0][1]['allow_redirects'])

    def test_stale_data_not_relabelled_fresh_and_open_flag_not_inferred(self):
        payload,now=fixture()
        old=self.fetch(payload,now+pd.Timedelta(hours=2))
        self.assertFalse(indicators(old,5,now+pd.Timedelta(hours=2))['fresh'])
        malformed=copy.deepcopy(payload); malformed['bars'][0]['isOpen']='false'
        with self.assertRaises(ValueError): self.fetch(malformed,now)
        malformed=copy.deepcopy(payload); malformed['bars'][0]['isOpen']=True
        with self.assertRaises(ValueError): self.fetch(malformed,now)

    def test_current_open_is_excluded_but_false_closed_flag_is_rejected(self):
        payload,now=fixture(label='4h')
        current=dict(openTime=now.isoformat(),open=100.,high=101.,low=99.,close=100.,isOpen=True)
        payload['bars'].insert(0,current)
        self.assertEqual(len(self.fetch(payload,now,label='4h')),80)
        current['isOpen']=False
        with self.assertRaises(ValueError): self.fetch(payload,now,label='4h')

    def test_envelope_shape_symbol_interval_and_numeric_corruption_rejected(self):
        payload,now=fixture()
        bad=[]
        for key,value in (('symbol','EURUSD'),('interval','4h'),('bars',{})):
            item=copy.deepcopy(payload); item[key]=value; bad.append(item)
        for value in (True,float('inf'),'100',None):
            item=copy.deepcopy(payload); item['bars'][0]['high']=value; bad.append(item)
        for item in bad:
            with self.subTest(item=str(item)[:40]):
                with self.assertRaises(ValueError): self.fetch(item,now)

    def test_duplicate_mixed_order_naive_time_http_and_body_budget_rejected(self):
        payload,now=fixture()
        bad=[]
        item=copy.deepcopy(payload); item['bars'][1]=item['bars'][0]; bad.append(item)
        item=copy.deepcopy(payload); item['bars'][1],item['bars'][2]=item['bars'][2],item['bars'][1]; bad.append(item)
        item=copy.deepcopy(payload); item['bars'][0]['openTime']='2026-11-01T00:00:00'; bad.append(item)
        for item in bad:
            with self.assertRaises(ValueError): self.fetch(item,now)
        with self.assertRaises(ValueError):
            fetch_biquote('USD/JPY','5m',now,get=lambda *a,**k:Response(payload,503))
        response=Response(payload); response.body=b' '*1_000_001
        with self.assertRaises(ValueError): fetch_biquote('USD/JPY','5m',now,get=lambda *a,**k:response)


if __name__=='__main__': unittest.main()
