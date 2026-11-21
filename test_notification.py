"""Consumer trust-boundary tests; no network or notification delivery."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

import notification


NOW = datetime(2026, 11, 18, 12, 15, tzinfo=timezone.utc)
SHA = 'a' * 40
PAIRS = ('USD/JPY', 'MXN/JPY', 'AUD/USD', 'EUR/USD', 'AUD/JPY')


def valid_inputs():
    report = {'schema_version': 2, 'generated_at': NOW.isoformat(),
              'published_at': NOW.isoformat(), 'mode': 'research_only',
              'decision': 'HOLD', 'acceptance': 'NOT_EVALUATED',
              'new_orders_allowed': False, 'data_status': 'PASS', 'pairs': {}}
    for pair in PAIRS:
        report['pairs'][pair] = {}
        for frame in ('5m', '15m', '4h'):
            report['pairs'][pair][frame] = {
                'fresh': True, 'quality_eligible': True, 'last_closed': NOW.isoformat(),
                'close': 1.1, 'wma': {'5': 1.1, '25': 1.1, '75': 1.1},
                'atr14': .1, 'bb20': {'mean': 1.1, 'bands': {
                    '1': [1., 1.2], '2': [.9, 1.3], '3': [.8, 1.4]}},
                'macd': {'line': -.1, 'signal': -.2, 'histogram': .1},
                'recent_low': 1., 'recent_high': 1.2}
    run = {'id': 123, 'status': 'completed', 'conclusion': 'success', 'head_sha': SHA}
    return report, run


class NotificationTests(unittest.TestCase):
    def validate(self, report=None, run=None, **kwargs):
        default_report, default_run = valid_inputs()
        return notification.validate_notification(
            default_report if report is None else report,
            default_run if run is None else run, NOW,
            expected_commit=kwargs.pop('expected_commit', SHA), **kwargs)

    def assert_hold_without_rates(self, result):
        self.assertFalse(result['accepted'])
        self.assertEqual(result['decision'], 'HOLD')
        self.assertIs(result['new_orders_allowed'], False)
        self.assertNotIn('pairs', result)
        self.assertNotIn('close', str(result))
        self.assertTrue(result['reasons'])

    def test_complete_current_research_is_accepted_without_order_permission(self):
        result = self.validate()
        self.assertTrue(result['accepted'])
        self.assertEqual(result['event_key'], '123:' + SHA)
        self.assertEqual(set(result['pairs']), set(PAIRS))
        self.assertIs(result['new_orders_allowed'], False)
        self.assertEqual(result['decision'], 'HOLD')

    def test_every_run_failure_or_commit_mismatch_discards_all_rates(self):
        for changes in ({'status': 'in_progress'}, {'conclusion': 'failure'},
                        {'head_sha': 'b' * 40}, {'id': 'untrusted-id'}, {'head_sha': None}):
            report, run = valid_inputs()
            run.update(changes)
            with self.subTest(changes=changes):
                self.assert_hold_without_rates(self.validate(report, run))
        self.assert_hold_without_rates(self.validate(expected_commit=None))

    def test_missing_pair_frame_or_unknown_schema_is_rejected(self):
        for mutate in (lambda r: r['pairs'].pop('USD/JPY'),
                       lambda r: r['pairs']['USD/JPY'].pop('4h'),
                       lambda r: r.update(schema_version=1),
                       lambda r: r['pairs'].update({'untrusted': {}})):
            report, run = valid_inputs()
            mutate(report)
            self.assert_hold_without_rates(self.validate(report, run))

    def test_all_report_and_candle_times_are_recomputed(self):
        for field in ('generated_at', 'published_at'):
            for value in ((NOW-timedelta(minutes=91)).isoformat(),
                          (NOW+timedelta(seconds=1)).isoformat(), 'not-time',
                          NOW.replace(tzinfo=None).isoformat()):
                report, run = valid_inputs()
                report[field] = value
                with self.subTest(field=field, value=value):
                    self.assert_hold_without_rates(self.validate(report, run))
        for frame, minutes in (('5m', 21), ('15m', 31), ('4h', 256)):
            report, run = valid_inputs()
            report['pairs']['USD/JPY'][frame]['last_closed'] = (NOW-timedelta(minutes=minutes)).isoformat()
            self.assert_hold_without_rates(self.validate(report, run))

    def test_candle_future_and_publication_before_generation_rejected(self):
        report, run = valid_inputs()
        report['pairs']['USD/JPY']['5m']['last_closed'] = (NOW+timedelta(seconds=1)).isoformat()
        self.assert_hold_without_rates(self.validate(report, run))
        report, run = valid_inputs()
        report['published_at'] = (NOW-timedelta(seconds=1)).isoformat()
        self.assert_hold_without_rates(self.validate(report, run))

    def test_flags_cannot_authorize_orders_or_mask_bad_quality(self):
        for changes in ({'decision': 'BUY'}, {'acceptance': 'PASS'},
                        {'new_orders_allowed': True}, {'new_orders_allowed': 0},
                        {'mode': 'live'}, {'data_status': 'FAIL'}):
            report, run = valid_inputs()
            report.update(changes)
            self.assert_hold_without_rates(self.validate(report, run))
        for field in ('fresh', 'quality_eligible'):
            for value in (False, 1, 'true'):
                report, run = valid_inputs()
                report['pairs']['EUR/USD']['15m'][field] = value
                self.assert_hold_without_rates(self.validate(report, run))

    def test_invalid_indicator_values_are_not_relayed(self):
        for value in (float('nan'), float('inf'), '1.1', True):
            report, run = valid_inputs()
            report['pairs']['USD/JPY']['5m']['close'] = value
            self.assert_hold_without_rates(self.validate(report, run))

    def test_nonfinite_values_in_every_nested_indicator_are_rejected(self):
        for mutate in (
                lambda row: row['wma'].update({'75': float('inf')}),
                lambda row: row['bb20']['bands'].update({'3': [float('nan'), 2.]}),
                lambda row: row['macd'].update({'histogram': float('-inf')}),
                lambda row: row.update(atr14=-1),
                lambda row: row.update(recent_low=2)):
            report, run = valid_inputs()
            mutate(report['pairs']['AUD/JPY']['4h'])
            self.assert_hold_without_rates(self.validate(report, run))

    def test_inconsistent_macd_identity_is_rejected_without_market_values(self):
        report, run = valid_inputs()
        report['pairs']['EUR/USD']['15m']['macd']['histogram'] = 999
        result = self.validate(report, run)
        self.assert_hold_without_rates(result)
        self.assertIn('INCONSISTENT_MACD', result['reasons'])

    def test_bb_nested_center_and_sigma_widths_must_agree(self):
        for n, bounds in (('3', [1.05, 1.15]), ('1', [1., 1.3]),
                          ('2', [.85, 1.35]), ('3', [.7, 1.5]),
                          ('2', [1., 1.2])):
            report, run = valid_inputs()
            report['pairs']['AUD/JPY']['4h']['bb20']['bands'][n] = bounds
            with self.subTest(n=n, bounds=bounds):
                result = self.validate(report, run)
                self.assert_hold_without_rates(result)
                self.assertIn('INCONSISTENT_BB_BANDS', result['reasons'])

    def test_indicator_semantic_checks_tolerate_roundoff_and_zero_variance(self):
        report, run = valid_inputs()
        row = report['pairs']['USD/JPY']['5m']
        row['macd']['histogram'] += 1e-13
        row['bb20']['bands']['2'][0] -= 1e-13
        self.assertTrue(self.validate(report, run)['accepted'])
        row['bb20']['bands'] = {str(n): [row['bb20']['mean'], row['bb20']['mean']]
                               for n in (1, 2, 3)}
        self.assertTrue(self.validate(report, run)['accepted'])

    def test_semantic_checks_fail_closed_if_finite_components_overflow(self):
        report, run = valid_inputs()
        report['pairs']['USD/JPY']['5m']['macd'] = {
            'line': 1e308, 'signal': -1e308, 'histogram': 1e308}
        self.assert_hold_without_rates(self.validate(report, run))

    def test_consumer_accepts_real_indicator_output_shape(self):
        import pandas as pd
        import fx

        report, run = valid_inputs()
        for pair in PAIRS:
            for label, minutes in (('5m', 5), ('15m', 15), ('4h', 240)):
                end = pd.Timestamp(NOW).floor(pd.Timedelta(minutes=minutes))
                index = pd.date_range(end=end-pd.Timedelta(minutes=minutes),
                                      periods=100, freq=pd.Timedelta(minutes=minutes))
                frame = pd.DataFrame({'Open': 1.1, 'High': 1.2, 'Low': 1., 'Close': 1.1}, index=index)
                report['pairs'][pair][label] = fx.indicators(frame, minutes, pd.Timestamp(NOW))
        result = self.validate(report, run)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['status'], 'VALIDATED_RESEARCH_ONLY')
        self.assertNotIn('candles', result['pairs']['USD/JPY']['5m'])

    def test_private_extra_fields_and_provider_errors_never_escape_allowlist(self):
        report, run = valid_inputs()
        report.update(capital='PRIVATE_SENTINEL', account='PRIVATE_SENTINEL',
                      stop_reasons=['PRIVATE_SENTINEL'])
        report['pairs']['USD/JPY']['5m']['capital'] = 'PRIVATE_SENTINEL'
        report['pairs']['USD/JPY']['5m']['wma']['account'] = 'PRIVATE_SENTINEL'
        run['secret'] = 'PRIVATE_SENTINEL'
        before = deepcopy(report)
        result = self.validate(report, run)
        self.assertTrue(result['accepted'])
        self.assertNotIn('PRIVATE_SENTINEL', str(result))
        self.assertEqual(report, before)
        report['pairs']['USD/JPY']['5m'] = {'error': 'PRIVATE_SENTINEL'}
        result = self.validate(report, run)
        self.assert_hold_without_rates(result)
        self.assertNotIn('PRIVATE_SENTINEL', str(result))

    def test_duplicate_event_returns_hold_without_old_prices(self):
        self.assert_hold_without_rates(self.validate(seen_event_keys={'123:' + SHA}))

    def test_malformed_inputs_fail_closed_without_exception_or_echo(self):
        for report in (None, [], 'PRIVATE_SENTINEL', {'pairs': None}):
            result = self.validate(report=report) if report is not None else notification.validate_notification(
                report, {}, NOW, expected_commit=SHA)
            self.assert_hold_without_rates(result)
            self.assertNotIn('PRIVATE_SENTINEL', str(result))
        report, run = valid_inputs()
        self.assert_hold_without_rates(notification.validate_notification(report, run, 'PRIVATE_SENTINEL', expected_commit=SHA))


if __name__ == '__main__':
    unittest.main()
