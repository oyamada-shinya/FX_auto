"""Synthetic risk fixtures; no personal profile or account values."""
import json
import unittest
from decimal import Decimal

from risk import evaluate_portfolio, validate_setting


LIMITS = {'max_floating_loss': '240', 'max_drawdown': '480',
          'min_maintenance_pct': '350', 'max_positions': 6}
LONG = {'pair': 'USD/JPY', 'side': 'buy', 'units': 2}
SHORT = {'pair': 'USD/JPY', 'side': 'sell', 'units': 2}


class PortfolioRiskTests(unittest.TestCase):
    def evaluate(self, **changes):
        args = dict(equity='8400', floating_pnl='-30', peak_equity='8500',
                    position_margin='1800', pending_margin='300',
                    positions=[LONG], limits=LIMITS.copy())
        args.update(changes)
        return evaluate_portfolio(**args)

    def test_broker_ratio_subtracts_pending_orders_before_division(self):
        result = self.evaluate()
        self.assertEqual(result['maintenance_pct'], 450.0)
        self.assertEqual(result['free_margin'], 6300.0)
        self.assertEqual(result['drawdown'], 100.0)
        self.assertEqual(result['floating_loss'], 30.0)
        self.assertTrue(result['new_orders_allowed'])
        json.dumps(result, allow_nan=False)

    def test_each_threshold_stops_at_exact_boundary(self):
        cases = [({'floating_pnl': '-240'}, 'FLOATING_LOSS_LIMIT'),
                 ({'peak_equity': '8880'}, 'DRAWDOWN_LIMIT'),
                 ({'pending_margin': '2100'}, 'MAINTENANCE_LIMIT'),
                 ({'positions': [LONG] * 6}, 'POSITION_LIMIT')]
        for change, reason in cases:
            with self.subTest(reason=reason):
                result = self.evaluate(**change)
                self.assertFalse(result['new_orders_allowed'])
                self.assertIn(reason, result['stop_reasons'])

    def test_hedged_positions_count_gross_not_net(self):
        limits = dict(LIMITS, max_positions=2)
        result = self.evaluate(positions=[LONG, SHORT], limits=limits)
        self.assertEqual(result['gross_positions'], 2)
        self.assertIn('POSITION_LIMIT', result['stop_reasons'])

    def test_zero_margin_is_json_null_not_infinity(self):
        result = self.evaluate(position_margin=0, positions=[])
        self.assertIsNone(result['maintenance_pct'])
        self.assertEqual(result['maintenance_status'], 'NO_POSITION_MARGIN')
        self.assertTrue(result['new_orders_allowed'])
        json.dumps(result, allow_nan=False)

    def test_positions_without_margin_cannot_pass(self):
        result = self.evaluate(position_margin=0)
        self.assertFalse(result['new_orders_allowed'])
        self.assertIn('POSITION_MARGIN_UNAVAILABLE', result['stop_reasons'])

    def test_insufficient_free_margin_stops_even_without_positions(self):
        result = self.evaluate(position_margin=0, positions=[], pending_margin='8400')
        self.assertFalse(result['new_orders_allowed'])
        self.assertIn('NO_FREE_MARGIN', result['stop_reasons'])

    def test_all_invalid_numeric_inputs_and_limits_are_rejected(self):
        for key in ('equity', 'floating_pnl', 'peak_equity', 'position_margin', 'pending_margin'):
            for value in (float('nan'), float('inf'), '-Infinity', True, None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.evaluate(**{key: value})
        for key in LIMITS:
            for value in (None, float('nan'), float('inf'), -1, 0, True):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.evaluate(limits=dict(LIMITS, **{key: value}))
            missing = LIMITS.copy()
            del missing[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.evaluate(limits=missing)

    def test_inconsistent_or_malformed_state_is_rejected(self):
        cases = [{'position_margin': -1}, {'pending_margin': -1},
                 {'peak_equity': '8399'}, {'positions': 2},
                 {'positions': [{'pair': 'USD/JPY', 'side': 'buy', 'units': 1.5}]},
                 {'positions': [{'pair': 'XXX/YYY', 'side': 'buy', 'units': 1}]},
                 {'positions': [{'pair': [], 'side': 'buy', 'units': 1}]},
                 {'limits': dict(LIMITS, max_positions=1.5)}]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.evaluate(**change)

    def test_decimal_boundary_does_not_drift(self):
        result = self.evaluate(floating_pnl=Decimal('-0.3'),
                               limits=dict(LIMITS, max_floating_loss=Decimal('0.3')))
        self.assertIn('FLOATING_LOSS_LIMIT', result['stop_reasons'])

    def test_negative_equity_stops(self):
        result = self.evaluate(equity='-1')
        self.assertFalse(result['new_orders_allowed'])
        self.assertIn('NON_POSITIVE_EQUITY', result['stop_reasons'])


class BrokerSettingTests(unittest.TestCase):
    def setting(self, **changes):
        args = dict(pair='USD/JPY', side='buy', lower='140', upper='140.2',
                    spacing_pips='10', profit_pips='10', units=2,
                    stop='139', max_positions=3)
        args.update(changes)
        return validate_setting(**args)

    def test_valid_setting_remains_hold_until_legacy_state_verified(self):
        result = self.setting()
        self.assertTrue(result['valid'])
        self.assertEqual(result['conservative_ladder_levels'], 3)
        self.assertFalse(result['new_orders_allowed'])
        self.assertIn('LEGACY_POSITIONS_UNVERIFIED', result['stop_reasons'])
        self.assertIn('PORTFOLIO_ACCEPTANCE_UNVERIFIED', result['stop_reasons'])
        json.dumps(result, allow_nan=False)

    def test_mexican_peso_specific_minimum_and_pip_conversion(self):
        result = self.setting(pair='MXN/JPY', lower='8', upper='8.06',
                              spacing_pips=3, profit_pips=3, stop='7.9')
        self.assertEqual(result['spacing_price'], 0.03)
        self.assertEqual(result['conservative_ladder_levels'], 3)
        for key in ('spacing_pips', 'profit_pips'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.setting(pair='MXN/JPY', lower='8', upper='8.06', stop='7.9',
                             **{key: '2.9'})

    def test_non_jpy_pip_and_tick_are_distinct(self):
        result = self.setting(pair='AUD/USD', lower='0.65', upper='0.652', stop='0.64')
        self.assertEqual(result['spacing_price'], 0.001)
        self.assertEqual(result['tick_size'], 0.00001)
        self.assertEqual(result['conservative_ladder_levels'], 3)

    def test_stop_is_beyond_adverse_edge(self):
        self.assertTrue(self.setting(side='sell', stop='141')['valid'])
        for change in ({'stop': '140'}, {'stop': None}, {'stop': '140.1'},
                       {'side': 'sell', 'stop': '140.2'}, {'side': 'sell', 'stop': '139'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.setting(**change)

    def test_invalid_range_ticks_units_widths_and_pairs_rejected(self):
        for change in ({'pair': 'GBP/JPY'}, {'side': 'hold'}, {'lower': 0},
                       {'lower': '140.201'}, {'upper': '140'}, {'lower': '140.0001'},
                       {'spacing_pips': '9.9'}, {'profit_pips': '9.9'},
                       {'spacing_pips': '10.01'}, {'profit_pips': '10.01'},
                       {'units': 0}, {'units': 1.1}, {'units': 2000001},
                       {'upper': float('inf')}, {'stop': float('nan')},
                       {'pair': []},
                       {'max_positions': 0}, {'max_positions': 101},
                       {'max_positions': 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.setting(**change)

    def test_finite_ladder_limit_and_broker_group_cap(self):
        result = self.setting(upper='149.9', max_positions=100)
        self.assertEqual(result['conservative_ladder_levels'], 100)
        with self.assertRaises(ValueError):
            self.setting(upper='150', max_positions=100)


if __name__ == '__main__':
    unittest.main()
