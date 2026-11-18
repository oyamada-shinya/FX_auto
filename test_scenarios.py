"""Invented stress fixtures only: correctness tests, never historical performance.

No personal settings are referenced. Economic-event timing, broker tick history,
real spreads/swaps, and the minimum historical acceptance period are unverified.
"""
import copy
import unittest

import pandas as pd

from backtest import run_backtest
from fx import indicators
from strategy import generate_candidates


def invented_profile():
    return dict(initial_cash=87000, max_floating_loss=600, max_drawdown=5000,
                min_maintenance_pct=350, max_positions=5, mode='scenario')


def invented_cost(spread=0):
    return dict(spread=spread, slippage=0, swap_long=0, swap_short=0, margin_rate=.04)


def bars(rows):
    return pd.DataFrame(rows, columns=['Open', 'High', 'Low', 'Close'],
                        index=pd.date_range('2025-02-03', periods=len(rows), freq='1h', tz='UTC'))


def grid(price=100, side='buy', quantity=10):
    return dict(direction=side, lower=price, upper=price, spacing=1,
                take_profit=2, stop=price-20 if side=='buy' else price+20,
                quantity=quantity, max_positions=1)


def hypothesis_space():
    return dict(range_atr=[1], spacing_atr=[1], profit_atr=[1], stop_atr=[2],
                quantities=[1], position_caps=[5], squeeze_ratio=.001)


def synthetic_indicator_report(direction=1):
    now = pd.Timestamp('2026-11-18T12:00:00Z')
    report = {}
    for label, minutes in (('5m', 5), ('15m', 15), ('4h', 240)):
        close = [100 + direction * i * .02 for i in range(100)]
        index = pd.date_range(end=now-pd.Timedelta(minutes=minutes), periods=len(close),
                              freq=pd.Timedelta(minutes=minutes))
        data = pd.DataFrame({'Open': close, 'Close': close,
                             'High': [value+.01 for value in close],
                             'Low': [value-.01 for value in close]}, index=index)
        report[label] = indicators(data, minutes, now)
    return report


class SyntheticScenarioTests(unittest.TestCase):
    def test_all_five_pairs_reverse_together_and_share_one_risk_stop(self):
        anchors = {'USD/JPY': 120., 'MXN/JPY': 6., 'AUD/JPY': 80.,
                   'AUD/USD': .7, 'EUR/USD': 1.1}
        settings, costs, frames = {}, {}, {}
        for pair, price in anchors.items():
            frames[pair] = bars([[price,price,price,price],
                                 [price,price,price*.999,price],
                                 [price,price,price*.9,price*.9],
                                 [price,price,price*.9,price]])
            settings[pair] = grid(price, quantity=20)
            settings[pair].update(stop=price*.5, take_profit=price)
            costs[pair] = invented_cost()
        profile = invented_profile()
        combined = run_backtest(frames, settings, profile, costs)
        self.assertTrue(combined['metrics']['stopped'])
        self.assertIn('FLOATING_LOSS_LIMIT', combined['metrics']['stop_reasons'])
        self.assertEqual(combined['metrics']['trades'], len(anchors))
        self.assertEqual(combined['metrics']['ending_positions'], 0)
        self.assertEqual(combined['metrics']['max_positions'], len(anchors))
        # Independent cash accounting at the adverse liquidation prices.
        expected = sum((price*.9-price)*20*(120 if pair.endswith('/USD') else 1)
                       for pair, price in anchors.items())
        self.assertAlmostEqual(combined['metrics']['net_profit'], expected)
        for pair in anchors:
            required = {pair: frames[pair]}
            if pair.endswith('/USD'):
                required['USD/JPY'] = frames['USD/JPY']
            isolated = run_backtest(required, {pair: settings[pair]}, profile, {pair: costs[pair]})
            self.assertFalse(isolated['metrics']['stopped'], pair)
            self.assertLess(isolated['metrics']['max_floating_loss'], profile['max_floating_loss'])
        self.assertFalse(combined['economic_accepted'])
        self.assertIn('synthetic_or_short_scenario_not_economic_acceptance', combined['reasons'])

    def test_spread_widening_reduces_net_equity_even_without_realized_losses(self):
        data = {'USD/JPY': bars([[100,100,100,100]] * 3)}
        settings = {'USD/JPY': grid()}
        narrow = run_backtest(data, settings, invented_profile(), {'USD/JPY': invented_cost(.02)})
        wide = run_backtest(data, settings, invented_profile(), {'USD/JPY': invented_cost(.4)})
        self.assertLess(wide['metrics']['net_profit'], narrow['metrics']['net_profit'])
        self.assertEqual(wide['metrics']['realized_profit'], 0)
        self.assertAlmostEqual(wide['metrics']['net_profit'], -.4*settings['USD/JPY']['quantity'])
        self.assertGreater(wide['metrics']['spread_slippage_total'], narrow['metrics']['spread_slippage_total'])
        self.assertEqual(wide['metrics']['ending_positions'], 1)

    def test_short_position_with_both_stop_and_target_touch_takes_adverse_exit(self):
        setting = grid(side='sell')
        setting['stop'] = 105
        result = run_backtest({'USD/JPY': bars([[100,100,100,100],
                                               [100,101,99,100],
                                               [100,106,97,100],
                                               [100,103,97,100]])},
                              {'USD/JPY': setting}, invented_profile(), {'USD/JPY': invented_cost()})
        self.assertEqual(result['metrics']['trades'], 1)
        self.assertEqual(result['metrics']['wins'], 0)
        self.assertAlmostEqual(result['metrics']['net_profit'], (100-105)*setting['quantity'])
        self.assertEqual(result['metrics']['ending_positions'], 0)
        self.assertIn('USD/JPY', result['metrics']['stopped_pairs'])

    def test_monotonic_adverse_trend_never_adds_beyond_finite_inventory(self):
        setting = grid(quantity=5)
        setting.update(lower=98, upper=100, spacing=1, stop=80,
                       take_profit=20, max_positions=2)
        profile = invented_profile()
        profile['max_positions'] = 2
        outcomes = []
        for length in (8, 12):
            rows = [[100-i,100-i+.2,100-i-.2,100-i] for i in range(length)]
            result = run_backtest({'USD/JPY': bars(rows)}, {'USD/JPY': setting},
                                  profile, {'USD/JPY': invented_cost()})
            self.assertEqual(result['metrics']['max_positions'], 2)
            self.assertEqual(result['metrics']['ending_positions'], 2)
            self.assertEqual(result['metrics']['trades'], 0)
            self.assertFalse(result['metrics']['stopped'])
            outcomes.append(result)
        self.assertLess(outcomes[1]['metrics']['net_profit'], outcomes[0]['metrics']['net_profit'])
        self.assertGreater(outcomes[1]['metrics']['max_open_holding_hours'], outcomes[0]['metrics']['max_open_holding_hours'])

    def test_repeating_range_does_not_profit_and_replenish_within_the_same_bar(self):
        result = run_backtest({'USD/JPY': bars([[100,100,100,100],
                                               [100,103,99,100],
                                               [100,103,99,100],
                                               [100,103,99,100]])},
                              {'USD/JPY': grid()}, invented_profile(), {'USD/JPY': invented_cost()})
        self.assertEqual(result['metrics']['trades'], 1)
        self.assertEqual(result['metrics']['ending_positions'], 1)
        self.assertEqual(result['metrics']['max_positions'], 1)
        self.assertAlmostEqual(result['metrics']['net_profit'], 2*grid()['quantity'])
        self.assertFalse(result['economic_accepted'])

    def test_synthetic_squeeze_holds_despite_otherwise_aligned_trend(self):
        report = synthetic_indicator_report()
        for row in report.values():
            mean = row['bb20']['mean']
            row['bb20']['bands'] = {str(n): [mean-.0001*n, mean+.0001*n] for n in (1,2,3)}
        result = generate_candidates('USD/JPY', report, hypothesis_space())
        self.assertEqual(result['candidates'], [])
        self.assertTrue(any('BB_SQUEEZE_HYPOTHESIS_HOLD' in reason for reason in result['reasons']))
        self.assertIs(result['new_orders_allowed'], False)
        self.assertEqual(result['decision'], 'HOLD')

    def test_synthetic_bandwalk_requires_timeframe_agreement_and_stays_research_only(self):
        report = synthetic_indicator_report()
        for row in report.values():
            self.assertGreater(row['close'], row['bb20']['bands']['1'][1])
        aligned = generate_candidates('USD/JPY', report, hypothesis_space())
        self.assertTrue(aligned['candidates'])
        self.assertEqual(aligned['decision'], 'HOLD')
        self.assertFalse(aligned['new_orders_allowed'])
        self.assertFalse(aligned['economic_accepted'])
        disagreement = copy.deepcopy(report)
        disagreement['4h'] = synthetic_indicator_report(-1)['4h']
        rejected = generate_candidates('USD/JPY', disagreement, hypothesis_space())
        self.assertEqual(rejected['candidates'], [])
        self.assertIn('TIMEFRAME_DIRECTION_DISAGREEMENT', rejected['reasons'])


if __name__ == '__main__':
    unittest.main()
