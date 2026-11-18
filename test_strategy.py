import copy
import unittest
import pandas as pd

from strategy import generate_candidates, walk_forward


def report(side='buy'):
    wma = {'5':101,'25':100,'75':99} if side=='buy' else {'5':99,'25':100,'75':101}
    macd = {'line':1,'signal':.5,'histogram':.5} if side=='buy' else {'line':-1,'signal':-.5,'histogram':-.5}
    item = dict(fresh=True,quality_eligible=True,close=100,atr14=.5,age_minutes=1,
                wma=wma,macd=macd,bb20={'mean':100,'bands':{'1':[99.5,100.5],'2':[99,101],'3':[98.5,101.5]}})
    return {name:copy.deepcopy(item) for name in ('5m','15m','4h')}


def search():
    return dict(range_atr=[1],spacing_atr=[1],profit_atr=[1],stop_atr=[2],
                quantities=[1],position_caps=[5],squeeze_ratio=.001)


def series(n=20):
    return pd.DataFrame({'Open':[100.]*n,'High':[100.1]*n,'Low':[99.9]*n,'Close':[100.]*n},
                        index=pd.date_range('2025-01-01',periods=n,freq='1h',tz='UTC'))


def profile(mode='scenario'):
    return dict(initial_cash=50000,max_floating_loss=2000,max_drawdown=4000,min_maintenance_pct=300,max_positions=5,mode=mode)


def costs():
    return {'USD/JPY':dict(spread=0,slippage=0,swap_long=0,swap_short=0,margin_rate=.04)}


class StrategyTests(unittest.TestCase):
    def test_aligned_buy_generates_broker_valid_research_only_candidates(self):
        result = generate_candidates('USD/JPY',report(),search())
        self.assertEqual(result['decision'],'HOLD')
        self.assertFalse(result['new_orders_allowed'])
        self.assertTrue(result['candidates'])
        candidate = result['candidates'][0]
        self.assertEqual(candidate['backtest_settings']['USD/JPY']['direction'],'buy')
        self.assertTrue(candidate['broker_validation']['valid'])

    def test_sell_direction_and_stop_are_symmetric(self):
        result = generate_candidates('USD/JPY',report('sell'),search())
        candidate = result['candidates'][0]['backtest_settings']['USD/JPY']
        self.assertEqual(candidate['direction'],'sell')
        self.assertGreater(candidate['stop'],candidate['upper'])

    def test_disagreement_squeeze_and_stale_all_hold_without_candidates(self):
        values = []
        bad=report(); bad['5m']=report('sell')['5m']; values.append(bad)
        bad=report(); bad['4h']['quality_eligible']=False; values.append(bad)
        bad=report(); bad['15m']['fresh']=False; values.append(bad)
        bad=report(); bad['5m']['macd']['histogram']=0; values.append(bad)
        bad=report(); bad['4h']['bb20']['bands']['2']=[99.999,100.001]; values.append(bad)
        for value in values:
            with self.subTest(value=value):
                result=generate_candidates('USD/JPY',value,search())
                self.assertEqual(result['candidates'],[])

    def test_missing_external_quantity_or_nonfinite_data_is_rejected(self):
        space=search(); del space['quantities']
        with self.assertRaises(ValueError): generate_candidates('USD/JPY',report(),space)
        bad=report(); bad['5m']['atr14']=float('nan')
        self.assertEqual(generate_candidates('USD/JPY',bad,search())['candidates'],[])

    def test_search_is_finite_and_bounded(self):
        space=search(); space['range_atr']=[1]*101
        with self.assertRaises(ValueError): generate_candidates('USD/JPY',report(),space)

    def test_valid_narrow_bands_trigger_squeeze_reason(self):
        bad=report()
        for name in bad:
            bad[name]['bb20']['bands']={str(n):[100-.0001*n,100+.0001*n] for n in (1,2,3)}
        result=generate_candidates('USD/JPY',bad,search())
        self.assertFalse(result['candidates'])
        self.assertTrue(any('BB_SQUEEZE' in reason for reason in result['reasons']))

    def test_non_jpy_adapter_converts_pip_width_to_price(self):
        data=report()
        for item in data.values():
            item['close']=1.05; item['atr14']=.001
            item['wma']={'5':1.06,'25':1.05,'75':1.04}
            item['bb20']={'mean':1.05,'bands':{str(n):[1.05-.002*n,1.05+.002*n] for n in (1,2,3)}}
        result=generate_candidates('EUR/USD',data,search())
        candidate=result['candidates'][0]
        self.assertAlmostEqual(candidate['backtest_settings']['EUR/USD']['spacing'],
                               candidate['broker_widths']['spacing_pips']*.0001)

    def test_wf_oos_timestamps_are_disjoint_and_after_training(self):
        candidates=[generate_candidates('USD/JPY',report(),search())['candidates'][0]['backtest_settings']]
        result=walk_forward({'USD/JPY':series()},candidates,profile(),costs(),train_bars=8,test_bars=4)
        self.assertEqual(len(result['folds']),3)
        self.assertFalse(result['economic_accepted'])
        for fold in result['folds']:
            self.assertLess(pd.Timestamp(fold['train_end']),pd.Timestamp(fold['test_start']))
        for left,right in zip(result['folds'],result['folds'][1:]):
            self.assertLess(pd.Timestamp(left['test_end']),pd.Timestamp(right['test_start']))

    def test_historical_fold_never_bypasses_90_day_gate(self):
        candidate=generate_candidates('USD/JPY',report(),search())['candidates'][0]['backtest_settings']
        result=walk_forward({'USD/JPY':series()},[candidate],profile('historical'),costs(),8,4)
        self.assertEqual(result['status'],'HOLD')
        self.assertTrue(all(fold['status']=='HOLD' for fold in result['folds']))
        self.assertTrue(all(fold['test_result'] is None for fold in result['folds']))

    def test_oos_mutation_does_not_change_first_train_selection(self):
        candidate=generate_candidates('USD/JPY',report(),search())['candidates'][0]['backtest_settings']
        f=series(); before=walk_forward({'USD/JPY':f},[candidate],profile(),costs(),8,4)
        changed=f.copy(); changed.iloc[8:]=[200,201,199,200]
        after=walk_forward({'USD/JPY':changed},[candidate],profile(),costs(),8,4)
        self.assertEqual(before['folds'][0]['selection'],after['folds'][0]['selection'])


if __name__=='__main__': unittest.main()
