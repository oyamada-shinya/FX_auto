import unittest
from unittest.mock import patch
import backtest
import pandas as pd
from backtest import run_backtest, chronological_split, select_training_candidate


def frame(rows, start='2025-01-01', freq='1h'):
    return pd.DataFrame(rows, columns=['Open', 'High', 'Low', 'Close'],
                        index=pd.date_range(start, periods=len(rows), freq=freq, tz='UTC'))


def profile(mode='scenario'):
    return dict(initial_cash=50000, max_floating_loss=2000, max_drawdown=4000,
                min_maintenance_pct=300, max_positions=4, mode=mode)


def setting():
    return dict(direction='buy', lower=100, upper=100, spacing=1, take_profit=2,
                stop=95, quantity=10, max_positions=1)


def cost():
    return dict(spread=0, slippage=0, swap_long=0, swap_short=0, margin_rate=.04)


class BacktestTests(unittest.TestCase):
    def run_case(self, rows, **kwargs):
        return run_backtest({'USD/JPY': frame(rows)}, {'USD/JPY': kwargs.get('setting', setting())},
                            kwargs.get('profile', profile()), {'USD/JPY': kwargs.get('cost', cost())})

    def test_short_history_holds_historical_acceptance(self):
        result = self.run_case([[100,101,99,100]]*4, profile=profile('historical'))
        self.assertEqual(result['status'], 'HOLD')
        self.assertIn('history_below_90_days', result['reasons'])
        self.assertIsNone(result['metrics'])

    def test_next_bar_only_and_stop_first(self):
        result = self.run_case([[100,110,90,100], [100,103,94,100]])
        self.assertEqual(result['metrics']['trades'], 1)
        self.assertAlmostEqual(result['metrics']['net_profit'], -50)
        self.assertFalse(result['economic_accepted'])

    def test_new_entry_cannot_take_same_bar_profit(self):
        result = self.run_case([[100,100,100,100], [100,104,99,101]])
        self.assertEqual(result['metrics']['trades'], 0)
        self.assertEqual(result['metrics']['ending_positions'], 1)
        self.assertAlmostEqual(result['metrics']['net_profit'], 10)

    def test_gap_stop_is_filled_at_adverse_open(self):
        result = self.run_case([[100,100,100,100], [100,101,99,100], [90,92,89,91]])
        self.assertAlmostEqual(result['metrics']['net_profit'], -100)

    def test_open_loss_and_costs_are_in_net_equity(self):
        c = cost(); c.update(spread=.2, slippage=.1)
        result = self.run_case([[100,100,100,100], [100,101,99,99]], cost=c)
        self.assertAlmostEqual(result['metrics']['net_profit'], -14)
        self.assertEqual(result['metrics']['realized_profit'], 0)

    def test_missing_costs_and_private_profile_fail_closed(self):
        with self.assertRaises(ValueError):
            run_backtest({'USD/JPY': frame([[100,101,99,100]]*4)}, {'USD/JPY': setting()}, {}, {'USD/JPY': cost()})
        c = cost(); del c['swap_short']
        with self.assertRaises(ValueError):
            self.run_case([[100,101,99,100]]*4, cost=c)

    def test_usd_quote_requires_synchronized_conversion(self):
        s = setting(); s.update(lower=1, upper=1, stop=.8, take_profit=.1)
        with self.assertRaises(ValueError):
            run_backtest({'EUR/USD': frame([[1,1.1,.9,1]]*4)}, {'EUR/USD': s}, profile(), {'EUR/USD': cost()})

    def test_usd_profit_is_converted_to_yen(self):
        s = setting(); s.update(lower=1, upper=1, stop=.8, take_profit=.1)
        prices = frame([[1,1,.99,1], [1,1.05,.99,1], [1.1,1.11,1.09,1.1]])
        conversion = frame([[150,150,150,150]]*3)
        result = run_backtest({'EUR/USD':prices, 'USD/JPY':conversion}, {'EUR/USD':s}, profile(), {'EUR/USD':cost()})
        self.assertAlmostEqual(result['metrics']['realized_profit'], 150)

    def test_gap_conversion_uses_open_not_future_close(self):
        s = setting(); s.update(lower=1, upper=1, stop=.8, take_profit=.1)
        prices = frame([[1,1,.99,1], [1,1.05,.99,1], [.7,.75,.69,.72]])
        conversion = frame([[150,150,150,150], [150,150,150,150], [150,200,140,200]])
        result = run_backtest({'EUR/USD': prices, 'USD/JPY':conversion}, {'EUR/USD':s}, profile(), {'EUR/USD':cost()})
        self.assertAlmostEqual(result['metrics']['realized_profit'], -450)

    def test_open_margin_gating_ignores_future_conversion_close(self):
        s=setting(); s.update(lower=1,upper=1,stop=.8,take_profit=.5)
        p=profile(); p['initial_cash']=1000
        prices=frame([[1,1,.99,1],[1,1.01,.99,1],[1,1.01,.99,1]])
        usd=frame([[150,150,150,150],[150,150,150,150],[150,1000,150,150]])
        changed=usd.copy(); changed.iloc[-1,changed.columns.get_loc('Close')]=1000
        before=run_backtest({'EUR/USD':prices,'USD/JPY':usd},{'EUR/USD':s},p,{'EUR/USD':cost()})
        after=run_backtest({'EUR/USD':prices,'USD/JPY':changed},{'EUR/USD':s},p,{'EUR/USD':cost()})
        self.assertAlmostEqual(before['metrics']['realized_profit'],after['metrics']['realized_profit'])
        self.assertLess(after['metrics']['realized_profit'],0)

    def test_direct_api_rejects_boolean_ohlc_and_object_boolean(self):
        for f in (frame([[True,True,True,True]]*3),frame([[1.,1.,1.,1.]]*3).astype(object)):
            f.iloc[0,0]=True
            with self.subTest(dtypes=str(f.dtypes)):
                with self.assertRaises(ValueError):
                    run_backtest({'USD/JPY':f},{'USD/JPY':setting()},profile(),{'USD/JPY':cost()})

    def test_work_budget_rejects_before_simulation(self):
        with patch.object(backtest,'MAX_WORK_UNITS',2,create=True):
            with self.assertRaisesRegex(ValueError,'work budget'):
                self.run_case([[100,101,99,100]]*4)

    def test_engine_position_envelope_is_explicit(self):
        p=profile(); p['max_positions']=1001
        with self.assertRaisesRegex(ValueError,'research engine position'):
            self.run_case([[100,101,99,100]]*4,profile=p)

    def test_signed_swap_is_in_both_cash_and_trade_expectancy(self):
        c = cost(); c['swap_long'] = -.01
        f = frame([[100,100,100,100], [100,101,99,100], [102,103,101,102]], start='2025-01-01 22:00')
        result = run_backtest({'USD/JPY':f}, {'USD/JPY':setting()}, profile(), {'USD/JPY':c})
        self.assertAlmostEqual(result['metrics']['net_profit'], 19.9)
        self.assertAlmostEqual(result['metrics']['expectancy'], 19.9)

    def test_open_position_age_is_reported(self):
        result = self.run_case([[100,100,100,100], [100,101,99,100], [100,101,99,100]])
        self.assertEqual(result['metrics']['max_open_holding_hours'], 1)

    def test_swap_open_breach_stops_before_reentry(self):
        c = cost(); c['swap_long'] = -20
        p = profile(); p['max_drawdown'] = 100
        f = frame([[100,100,100,100], [100,101,99,100], [100,101,99,100]], start='2025-01-01 22:00')
        result = run_backtest({'USD/JPY':f}, {'USD/JPY':setting()}, p, {'USD/JPY':c})
        self.assertTrue(result['metrics']['stopped'])
        self.assertEqual(result['metrics']['trades'], 1)
        self.assertEqual(result['metrics']['ending_positions'], 0)

    def test_sparse_90_day_span_is_not_history_coverage(self):
        f = frame([[100,101,99,100]]*2, freq='100D')
        result = run_backtest({'USD/JPY':f}, {'USD/JPY':setting()}, profile('historical'), {'USD/JPY':cost()})
        self.assertEqual(result['status'], 'HOLD')
        self.assertIsNone(result['metrics'])
        self.assertIn('historical_cadence_or_coverage_unverified',result['reasons'])

    def test_pending_ladder_margin_blocks_overallocation(self):
        s = setting(); s.update(quantity=100000)
        result = self.run_case([[100,100,100,100]]*3, setting=s)
        self.assertTrue(result['metrics']['stopped'])
        self.assertEqual(result['metrics']['trades'],0)
        self.assertIn('NO_FREE_MARGIN',result['metrics']['stop_reasons'])

    def test_combined_position_cap_stops_additions(self):
        p = profile(); p['max_positions'] = 1
        f = frame([[100,101,99,100]]*4)
        result = run_backtest({'USD/JPY':f, 'AUD/JPY':f}, {'USD/JPY':setting(),'AUD/JPY':setting()}, p,
                              {'USD/JPY':cost(),'AUD/JPY':cost()})
        self.assertLessEqual(result['metrics']['max_positions'], 1)

    def test_risk_stop_is_permanent(self):
        p = profile(); p['max_floating_loss'] = 10
        result = self.run_case([[100,100,100,100], [100,101,98,99], [100,101,98,99], [100,101,98,99]], profile=p)
        self.assertTrue(result['metrics']['stopped'])
        self.assertEqual(result['metrics']['ending_positions'], 0)
        self.assertEqual(result['metrics']['trades'], 1)

    def test_favorable_then_adverse_intrabar_drawdown_is_bounded(self):
        s = setting(); s['take_profit'] = 1000
        p = profile(); p['max_drawdown'] = 500
        result = self.run_case([[100,100,100,100], [100,200,100,100]], setting=s, profile=p)
        self.assertTrue(result['metrics']['stopped'])
        self.assertAlmostEqual(result['metrics']['max_drawdown'],1000)

    def test_favorable_stress_margin_breach_is_not_ignored(self):
        p=profile(); p.update(initial_cash=5000,max_floating_loss=200000,max_drawdown=200000,max_positions=2)
        s=setting(); s.update(take_profit=2000,stop=1)
        c=cost(); c['margin_rate']=1
        result=self.run_case([[100,100,100,100],[100,1000,99,100]],setting=s,profile=p,cost=c)
        self.assertLessEqual(result['metrics']['min_maintenance_pct'],300)
        self.assertTrue(result['metrics']['stopped'])
        self.assertIn('MAINTENANCE_LIMIT',result['metrics']['stop_reasons'])

    def test_chronological_split_is_disjoint_and_aligned(self):
        f = frame([[100,101,99,100]]*10)
        train, test = chronological_split({'USD/JPY':f}, .6)
        self.assertLess(train['USD/JPY'].index[-1], test['USD/JPY'].index[0])
        self.assertEqual(len(train['USD/JPY']) + len(test['USD/JPY']), len(f))

    def test_training_selection_does_not_receive_test_data(self):
        f = frame([[100,101,99,100]]*10)
        selected = select_training_candidate({'USD/JPY': f}, [{'USD/JPY': setting()}], profile(), {'USD/JPY':cost()})
        self.assertEqual(selected['candidate_index'], 0)
        self.assertEqual(selected['status'], 'RESEARCH_ONLY')


if __name__ == '__main__': unittest.main()
