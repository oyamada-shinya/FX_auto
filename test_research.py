"""Local-only research CLI tests using invented profiles and temporary files."""
import contextlib
import io
import hashlib
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import research


def invented_config(csv_path):
    return {
        'profile': {'initial_cash': 72000, 'max_floating_loss': 1700,
                    'max_drawdown': 3300, 'min_maintenance_pct': 360,
                    'max_positions': 3, 'mode': 'scenario'},
        'settings': {'USD/JPY': {'direction': 'buy', 'lower': 120, 'upper': 120,
                                'spacing': 1, 'take_profit': 2, 'stop': 115,
                                'quantity': 5, 'max_positions': 1}},
        'costs': {'USD/JPY': {'spread': 0.1, 'slippage': 0.1, 'swap_long': -0.01,
                             'swap_short': -0.02, 'margin_rate': 0.04}},
        'data': {'USD/JPY': str(csv_path)},
    }


class ResearchCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / 'invented.csv'
        self.csv.write_text('timestamp,Open,High,Low,Close\n'
                            '2025-02-03T00:00:00Z,120,121,119,120\n'
                            '2025-02-03T01:00:00Z,120,121,119,120\n'
                            '2025-02-03T02:00:00Z,120,123,119,122\n', encoding='utf-8')
        self.config = self.root / 'DO_NOT_DISCLOSE_PROFILE.json'
        self.config.write_text(json.dumps(invented_config(self.csv)), encoding='utf-8')
        self.output = self.root / 'private-result.json'

    def call(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = research.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_loader_uses_explicit_profile_costs_and_relative_utc_csv(self):
        self.config.write_text(json.dumps(invented_config('invented.csv')), encoding='utf-8')
        loaded = research.load_inputs(self.config)
        self.assertEqual(loaded['profile']['initial_cash'], 72000)
        self.assertEqual(str(loaded['frames']['USD/JPY'].index.tz), 'UTC')
        self.assertEqual(len(loaded['frames']['USD/JPY']), 3)
        self.assertEqual(loaded['costs']['USD/JPY']['spread'], 0.1)

    def test_real_cli_writes_private_result_but_stdout_is_redacted(self):
        result = subprocess.run([sys.executable, str(Path(research.__file__)),
                                 '--config', str(self.config), '--output', str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout), {'status': 'HOLD', 'economic_accepted': False})
        self.assertEqual(result.stderr, '')
        report = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertFalse(report['economic_accepted'])
        self.assertIsNotNone(report['metrics'])
        self.assertNotIn('72000', result.stdout)
        self.assertNotIn(str(self.config), result.stdout)

    def test_historical_short_data_stays_hold(self):
        config = invented_config(self.csv)
        config['profile']['mode'] = 'historical'
        self.config.write_text(json.dumps(config), encoding='utf-8')
        code, out, err = self.call(['--config', str(self.config), '--output', str(self.output)])
        self.assertEqual(code, 0)
        report = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'HOLD')
        self.assertIn('history_below_90_days', report['reasons'])
        self.assertFalse(json.loads(out)['economic_accepted'])
        self.assertEqual(err, '')

    def test_walk_forward_cli_audits_grid_and_protects_it_as_input(self):
        timeline = pd.date_range('2026-07-06', periods=92*6, freq='4h', tz='UTC')
        grid = timeline[timeline.dayofweek < 5]
        schedule = self.root / 'expected.csv'
        schedule.write_text('timestamp\n' + ''.join(f'{stamp.isoformat()}\n' for stamp in grid))
        self.csv.write_text('timestamp,Open,High,Low,Close\n'
                            + ''.join(f'{stamp.isoformat()},100,101,99,100\n' for stamp in grid))
        config = invented_config(self.csv)
        config['profile']['mode'] = 'historical'
        self.config.write_text(json.dumps(config), encoding='utf-8')
        options = ['--config', str(self.config), '--session-grid', str(schedule),
                   '--bar-minutes', '240', '--train-bars', '100', '--test-bars', '50']
        code, out, err = self.call(options + ['--output', str(self.output)])
        self.assertEqual(code, 0)
        report = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'RESEARCH_ONLY')
        self.assertTrue(report['folds'])
        self.assertFalse(report['economic_accepted'])
        self.assertEqual(report['provenance']['session_grid_sha256'],
                         hashlib.sha256(schedule.read_bytes()).hexdigest())
        self.assertNotIn(str(schedule), out)
        self.assertEqual(err, '')
        code, out, err = self.call(options + ['--output', str(schedule)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)['error_code'], 'UNSAFE_OUTPUT_PATH')

    def test_missing_or_invalid_config_never_prints_path_or_values(self):
        for payload in (None, '{"secret":"SHOULD_NOT_APPEAR",',
                        '{"profile":{"initial_cash":Infinity}}',
                        '{"profile":{},"profile":{"secret":"SHOULD_NOT_APPEAR"}}'):
            with self.subTest(payload=payload):
                if payload is None:
                    self.config.unlink(missing_ok=True)
                else:
                    self.config.write_text(payload, encoding='utf-8')
                code, out, err = self.call(['--config', str(self.config), '--output', str(self.output)])
                self.assertNotEqual(code, 0)
                self.assertFalse(json.loads(out)['economic_accepted'])
                self.assertNotIn('SHOULD_NOT_APPEAR', out + err)
                self.assertNotIn('DO_NOT_DISCLOSE', out + err)
                self.assertFalse(self.output.exists())

    def test_missing_profile_fields_are_not_defaulted(self):
        config = invented_config(self.csv)
        del config['profile']['initial_cash']
        self.config.write_text(json.dumps(config), encoding='utf-8')
        code, out, err = self.call(['--config', str(self.config), '--output', str(self.output)])
        self.assertNotEqual(code, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(err, '')

    def test_json_overflow_is_rejected_even_in_unused_fields(self):
        payload = json.dumps(invented_config(self.csv))
        payload = payload.replace('"mode": "scenario"', '"mode": "scenario", "unused": 1e400')
        self.config.write_text(payload, encoding='utf-8')
        with self.assertRaises(ValueError):
            research.load_inputs(self.config)

    def test_forexsb_export_requires_explicit_utc_format(self):
        self.csv.write_text('2026-08-21 00:00\t100\t101\t99\t100\t10\n'
                            '2026-08-21 00:10\t101\t102\t100\t101\t12\n',
                            encoding='utf-8')
        with self.assertRaisesRegex(research.PrivateInputError, 'INVALID_DATA'):
            research.load_inputs(self.config)
        config = invented_config(self.csv)
        config['data']['USD/JPY'] = {'path': self.csv.name, 'format': 'forexsb_utc'}
        self.config.write_text(json.dumps(config), encoding='utf-8')
        frame = research.load_inputs(self.config)['frames']['USD/JPY']
        self.assertEqual(str(frame.index.tz), 'UTC')
        self.assertEqual((frame.index[-1] - frame.index[0]).total_seconds(), 600)
        self.assertEqual(len(frame), 2)

    def test_accepts_dukascopy_utc_header_but_rejects_ambiguous_time(self):
        original = self.csv.read_text(encoding='utf-8')
        timestamp, *rows = original.splitlines()
        self.csv.write_text(original.replace('timestamp,', 'Etc/UTC,', 1), encoding='utf-8')
        frame = research.load_inputs(self.config)['frames']['USD/JPY']
        self.assertEqual(len(frame), len(rows))
        self.csv.write_text('\n'.join([timestamp + ',Etc/UTC'] +
                                      [row + ',' + row.split(',')[0] for row in rows]),
                            encoding='utf-8')
        with self.assertRaisesRegex(research.PrivateInputError, 'INVALID_DATA'):
            research.load_inputs(self.config)

    def test_csv_requires_utc_not_naive_or_non_utc_or_duplicate_timestamp(self):
        originals = self.csv.read_text(encoding='utf-8')
        for content in (originals.replace('Z', ''), originals.replace('Z', '+01:00'),
                        originals.replace('01:00:00Z', '00:00:00Z'),
                        originals.replace('timestamp,', 'time,')):
            self.csv.write_text(content, encoding='utf-8')
            with self.subTest(content=content), self.assertRaises(ValueError):
                research.load_inputs(self.config)

    def test_raw_duplicate_and_blank_csv_headers_are_rejected(self):
        original = self.csv.read_text(encoding='utf-8')
        cases = [original.replace('Close\n', 'Close,Close\n').replace(',120\n', ',120,999\n').replace(',122\n', ',122,999\n'),
                 original.replace('timestamp,', 'timestamp,timestamp,'),
                 original.replace('Close\n', 'Close,\n'),
                 original.replace('Close\n', 'Close, close\n')]
        for content in cases:
            self.csv.write_text(content, encoding='utf-8')
            with self.subTest(content=content), self.assertRaises(ValueError):
                research.load_inputs(self.config)

    def test_bool_ohlc_csv_is_not_a_numeric_price(self):
        self.csv.write_text('timestamp,Open,High,Low,Close\n'
                            '2025-02-03T00:00:00Z,True,True,True,True\n'
                            '2025-02-03T01:00:00Z,True,True,True,True\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            research.load_inputs(self.config)

    def test_csv_local_resource_budgets_fail_instead_of_truncating(self):
        with patch.object(research, 'MAX_CSV_BYTES', self.csv.stat().st_size - 1):
            with self.assertRaises(ValueError):
                research.load_inputs(self.config)
        with patch.object(research, 'MAX_CSV_ROWS', 2):
            with self.assertRaises(ValueError):
                research.load_inputs(self.config)
        with patch.object(research, 'MAX_CSV_ROWS', 3):
            self.assertEqual(len(research.load_inputs(self.config)['frames']['USD/JPY']), 3)

    def test_private_provenance_hashes_exact_input_bytes_and_omits_paths(self):
        code, out, err = self.call(['--config', str(self.config), '--output', str(self.output)])
        self.assertEqual(code, 0)
        provenance = json.loads(self.output.read_text(encoding='utf-8'))['provenance']
        self.assertEqual(provenance['config_sha256'], hashlib.sha256(self.config.read_bytes()).hexdigest())
        data = provenance['data']['USD/JPY']
        self.assertEqual(data['sha256'], hashlib.sha256(self.csv.read_bytes()).hexdigest())
        self.assertEqual(data['rows'], 3)
        self.assertEqual(data['first_timestamp'], '2025-02-03T00:00:00+00:00')
        self.assertEqual(data['last_timestamp'], '2025-02-03T02:00:00+00:00')
        self.assertNotIn(str(self.root), json.dumps(provenance))
        self.assertNotIn(data['sha256'], out + err)

    def test_public_output_is_rejected_and_ignored_private_directories_allowed(self):
        repo = Path(research.__file__).resolve().parent
        for output in (repo / 'report.json', repo / 'public-risk.json',
                       repo / 'docs' / 'derived.json', self.root / 'report.json'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                research.validate_output_path(output)
        self.assertEqual(research.validate_output_path(repo / 'private' / 'derived.json'),
                         (repo / 'private' / 'derived.json').resolve())
        self.assertEqual(research.validate_output_path(repo / 'backtest-results' / 'derived.json'),
                         (repo / 'backtest-results' / 'derived.json').resolve())

    def test_output_cannot_overwrite_input_profile_or_csv(self):
        for output in (self.config, self.csv):
            original = output.read_bytes()
            code, out, err = self.call(['--config', str(self.config), '--output', str(output)])
            self.assertNotEqual(code, 0)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(err, '')

    def test_output_alias_cannot_replace_hardlinked_input(self):
        os.link(self.csv, self.output)
        original = self.csv.read_bytes()
        code, out, err = self.call(['--config', str(self.config), '--output', str(self.output)])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.csv.read_bytes(), original)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(err, '')

    def test_serialization_failure_preserves_previous_report(self):
        self.output.write_text('old report', encoding='utf-8')
        with self.assertRaises(ValueError):
            research.write_private_report(self.output, {'bad': float('inf')})
        self.assertEqual(self.output.read_text(encoding='utf-8'), 'old report')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_argument_errors_do_not_echo_private_paths(self):
        code, out, err = self.call(['--config', 'SHOULD_NOT_APPEAR'])
        self.assertNotEqual(code, 0)
        self.assertNotIn('SHOULD_NOT_APPEAR', out + err)
        self.assertFalse(json.loads(out)['economic_accepted'])


if __name__ == '__main__':
    unittest.main()
