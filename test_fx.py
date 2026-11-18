import json
import math
import statistics
import unittest

import numpy as np
import pandas as pd

import fx


def candles(n=100):
    close = np.array([100 + i * .1 + math.sin(i) for i in range(n)])
    return pd.DataFrame({'Open': close, 'High': close + .7,
                         'Low': close - .8, 'Close': close},
                        index=pd.date_range('2026-11-16', periods=n, freq='5min', tz='UTC'))


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        self.df = candles()
        self.now = self.df.index[-1] + pd.Timedelta(minutes=5)

    def test_reference_calculations(self):
        result = fx.indicators(self.df, 5, self.now)
        close = self.df.Close.tolist()
        for n in (5, 25, 75):
            expected = np.average(close[-n:], weights=np.arange(1, n + 1))
            self.assertAlmostEqual(result['wma'][str(n)], expected, places=12)
        tr = [max(h-l, abs(h-p), abs(l-p)) for h, l, p in
              zip(self.df.High.tolist()[1:], self.df.Low.tolist()[1:], close)]
        tr.insert(0, self.df.High.iloc[0] - self.df.Low.iloc[0])
        # Independent closed-form Wilder smoothing: initial mean then decayed TRs.
        k = len(tr) - 14
        expected = statistics.mean(tr[:14]) * (13/14)**k
        expected += math.fsum(v / 14 * (13/14)**(k-1-j) for j, v in enumerate(tr[14:]))
        self.assertAlmostEqual(result['atr14'], expected, places=12)
        mean, sd = statistics.mean(close[-20:]), statistics.pstdev(close[-20:])
        for n in (1, 2, 3):
            np.testing.assert_allclose(result['bb20']['bands'][str(n)], [mean-n*sd, mean+n*sd], rtol=1e-13)

    def test_invalid_values_rejected_before_aggregation(self):
        for value in (np.inf, -np.inf, np.nan):
            with self.subTest(value=value):
                bad = self.df.copy()
                bad.iloc[30, bad.columns.get_loc('High')] = value
                with self.assertRaises(ValueError):
                    fx.indicators(bad, 5, self.now)

    def test_finite_input_cannot_emit_nonfinite_indicators(self):
        huge = self.df.copy()
        huge.loc[:, ['Open','High','Low','Close']] = 1e308
        with np.errstate(over='ignore', invalid='ignore'):
            with self.assertRaises(ValueError):
                fx.indicators(huge, 5, self.now)

    def test_boolean_prices_rejected(self):
        bad = pd.DataFrame(True, index=self.df.index, columns=['Open','High','Low','Close'])
        with self.assertRaises(ValueError):
            fx.indicators(bad, 5, self.now)

    def test_duplicates_rejected(self):
        with self.assertRaises(ValueError):
            fx.indicators(pd.concat([self.df, self.df.iloc[[20]]]), 5, self.now)

    def test_weekday_gap_rejected(self):
        with self.assertRaises(ValueError):
            fx.indicators(self.df.drop(self.df.index[30]), 5, self.now)

    def test_unclosed_bar_does_not_leak(self):
        before = fx.indicators(self.df.iloc[:-1], 5, self.now - pd.Timedelta(minutes=5))
        after = fx.indicators(self.df, 5, self.now - pd.Timedelta(minutes=5))
        self.assertEqual(before, after)

    def test_stale_report_is_not_fresh(self):
        self.assertFalse(fx.indicators(self.df, 5, self.now + pd.Timedelta(hours=2))['fresh'])

    def test_insufficient_history(self):
        with self.assertRaises(ValueError):
            fx.indicators(self.df.iloc[:74], 5, self.now)

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            fx.indicators(self.df.tz_localize(None), 5, self.now)

    def test_cross_date_gap_does_not_pass_quality(self):
        df = self.df.copy()
        df.index = pd.date_range('2026-11-16 20:00', periods=len(df), freq='5min', tz='UTC')
        df = df.drop(pd.Timestamp('2026-11-17', tz='UTC'))
        result = fx.indicators(df, 5, df.index[-1] + pd.Timedelta(minutes=5))
        self.assertFalse(result['quality_eligible'])

    def test_atomic_report_preserves_existing_on_bad_json(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report.json'
            path.write_text('{"valid":true}', encoding='utf-8')
            with self.assertRaises(ValueError):
                fx.write_report({'bad': float('inf')}, path)
            self.assertEqual(json.loads(path.read_text()), {'valid': True})

    def test_macd_reference(self):
        def ema(values, span):
            out = [values[0]]
            alpha = 2 / (span + 1)
            for value in values[1:]:
                out.append(out[-1] + alpha * (value - out[-1]))
            return out
        values = self.df.Close.tolist()
        line = [a-b for a, b in zip(ema(values, 12), ema(values, 26))]
        expected_signal = ema(line, 9)[-1]
        actual = fx.indicators(self.df, 5, self.now)['macd']
        self.assertAlmostEqual(actual['line'], line[-1], places=12)
        self.assertAlmostEqual(actual['signal'], expected_signal, places=12)
        self.assertAlmostEqual(actual['histogram'], line[-1]-expected_signal, places=12)


class PipelineTests(unittest.TestCase):
    def test_complete_report_and_provider_failure_are_safe(self):
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        now = pd.Timestamp('2026-11-18 12:00', tz='UTC')
        def provider(symbol, interval, period):
            minutes = {'5m':5, '15m':15, '1h':60}[interval]
            count = 400 if interval == '1h' else 100
            df = candles(count)
            df.index = pd.date_range(end=now-pd.Timedelta(minutes=minutes),
                                     periods=count, freq=f'{minutes}min', tz='UTC')
            return df
        def broken(*args):
            raise TimeoutError('private-token-must-not-appear')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'report.json'
            for fetcher, expected in ((provider, 0), (broken, 1)):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(fx.main(now, fetcher, path), expected)
                report = json.loads(path.read_text(encoding='utf-8'))
                self.assertFalse(report['new_orders_allowed'])
                self.assertEqual(report['decision'], 'HOLD')
                self.assertEqual(set(report['pairs']), set(fx.PAIRS))
                self.assertNotIn('private-token', output.getvalue())
                for pair in report['pairs'].values():
                    self.assertEqual(set(pair), {'5m','15m','4h'})
                    if expected == 0:
                        for label, source_interval in (('5m','5m'),('15m','15m'),('4h','1h')):
                            diagnostic = pair[label]['source_diagnostics']
                            self.assertEqual(diagnostic['interval'], source_interval)
                            self.assertEqual(len(diagnostic['normalized_ohlc_sha256']), 64)
                            self.assertGreater(diagnostic['received_candles'], 0)
                self.assertEqual(report['data_status'], 'PASS' if expected == 0 else 'FAIL')

    def test_four_hour_exact_slots_and_ohlc(self):
        df = candles(8)
        df.index = pd.date_range('2026-11-16', periods=8, freq='h', tz='UTC')
        now = df.index[-1] + pd.Timedelta(hours=1)
        result = fx.four_hour_bars(df, now)
        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[0].Close, df.iloc[3].Close)
        self.assertEqual(result.iloc[0].High, df.iloc[:4].High.max())
        for bad in (pd.concat([df.iloc[[0,1,1,3]], df.iloc[4:]]),
                    df.assign(High=[np.nan] + df.High.tolist()[1:])):
            with self.assertRaises(ValueError):
                fx.four_hour_bars(bad, now)

    def test_four_hour_partial_bucket_excluded(self):
        df = candles(7)
        df.index = pd.date_range('2026-11-16', periods=7, freq='h', tz='UTC')
        self.assertEqual(len(fx.four_hour_bars(df, df.index[-1]+pd.Timedelta(hours=1))), 1)

    def test_closed_terminal_four_hour_gap_blocks_quality(self):
        now = pd.Timestamp('2026-11-18 12:00', tz='UTC')
        df = candles(400)
        df.index = pd.date_range(end=now-pd.Timedelta(hours=1), periods=400, freq='h', tz='UTC')
        for missing in (1, 4):
            with self.subTest(missing=missing):
                result = fx.indicators(fx.four_hour_bars(df.iloc[:-missing], now), 240, now)
                self.assertFalse(result['quality_eligible'])
                self.assertEqual(result['incomplete_closed_bins'], 1)

    def test_transient_recovery_and_exhaustion(self):
        class Provider:
            def __init__(self, failures):
                self.failures = failures
                self.calls = 0
            def history(self, **kwargs):
                self.calls += 1
                if self.calls <= self.failures:
                    raise TimeoutError('injected')
                return candles()
        for failures in (1, 3):
            provider = Provider(failures)
            delays = []
            if failures == 1:
                self.assertFalse(fx.fetch_history('TEST', '5m', '5d', ticker=lambda _:provider, sleep=delays.append).empty)
                self.assertEqual(provider.calls, 2)
                self.assertEqual(delays, [1])
            else:
                with self.assertRaises(TimeoutError):
                    fx.fetch_history('TEST', '5m', '5d', ticker=lambda _:provider, sleep=delays.append)
                self.assertEqual(provider.calls, 3)
                self.assertEqual(delays, [1, 2])


if __name__ == '__main__':
    unittest.main()
