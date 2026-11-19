"""Local-only CSV backtest runner. Never publishes personal portfolio reports.

Usage: python research.py --config <private JSON> --output <private JSON path>
All monetary values, costs and candidate settings are explicit external inputs.
Public stdout always reports HOLD; this CLI cannot certify economic acceptance.
"""
import argparse
import contextlib
import csv
from datetime import timedelta
import io
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from backtest import COST_KEYS, PROFILE_KEYS, SETTING_KEYS, SUPPORTED, run_backtest


REPOSITORY_ROOT = Path(__file__).resolve().parent
# Local processing budgets, not reduced historical acceptance requirements.
# One year of five-minute records fits well below either per-file limit.
MAX_CSV_BYTES = 256 * 1024 * 1024
MAX_CSV_ROWS = 1_000_000


class PrivateInputError(ValueError):
    """A safe error category; original input values are never in its message."""


def _reject_constant(_value):
    raise PrivateInputError('INVALID_JSON')


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PrivateInputError('INVALID_JSON')
        result[key] = value
    return result


def _finite_json_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise PrivateInputError('INVALID_JSON')
    if isinstance(value, dict):
        for item in value.values():
            _finite_json_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json_tree(item)


def _finite_numbers(record, keys):
    if not isinstance(record, dict) or any(key not in record for key in keys):
        raise PrivateInputError('INVALID_CONFIGURATION')
    for key in keys:
        value = record[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            raise PrivateInputError('INVALID_CONFIGURATION')


def _read_config(path):
    try:
        raw = Path(path).read_bytes()
        config = json.loads(raw.decode('utf-8'), parse_constant=_reject_constant,
                            object_pairs_hook=_unique_keys)
        _finite_json_tree(config)
    except (OSError, ValueError, TypeError, OverflowError):
        raise PrivateInputError('INVALID_CONFIGURATION') from None
    if not isinstance(config, dict) or set(config) != {'profile', 'settings', 'costs', 'data'}:
        raise PrivateInputError('INVALID_CONFIGURATION')
    _finite_numbers(config['profile'], PROFILE_KEYS)
    if (not isinstance(config['settings'], dict) or not config['settings']
            or not isinstance(config['costs'], dict) or not isinstance(config['data'], dict)
            or not config['data']):
        raise PrivateInputError('INVALID_CONFIGURATION')
    for pair, setting in config['settings'].items():
        if pair not in SUPPORTED or pair not in config['data'] or pair not in config['costs']:
            raise PrivateInputError('INVALID_CONFIGURATION')
        _finite_numbers(setting, SETTING_KEYS)
        _finite_numbers(config['costs'][pair], COST_KEYS)
    return config, hashlib.sha256(raw).hexdigest()


def load_inputs(config_path):
    """Load explicit private configuration plus unique increasing UTC CSV bars.

    CSV paths resolve relative to the config file. Each CSV must have one
    timestamp column (timestamp or Dukascopy Etc/UTC) and
    Open, High, Low and Close columns. Naive/non-UTC timestamps are rejected;
    they are never silently localized. No profile/cost defaults are supplied.
    """
    config_path = Path(config_path).expanduser().resolve()
    config, config_sha256 = _read_config(config_path)
    frames = {}
    for pair, csv_path in config['data'].items():
        if pair not in SUPPORTED or not isinstance(csv_path, str) or not csv_path.strip():
            raise PrivateInputError('INVALID_DATA')
        path = Path(csv_path).expanduser()
        if not path.is_absolute():
            path = config_path.parent / path
        path = path.resolve()
        try:
            with path.open('rb') as stream:
                raw = stream.read(MAX_CSV_BYTES + 1)
            if len(raw) > MAX_CSV_BYTES:
                raise PrivateInputError('CSV_BYTE_BUDGET_EXCEEDED')
            # Check the original header before pandas can mangle duplicates.
            header = next(csv.reader(io.StringIO(raw.decode('utf-8-sig')), strict=True))
            normalized = [name.strip().casefold() for name in header]
            if not normalized or any(not name for name in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError
            frame = pd.read_csv(io.BytesIO(raw), nrows=MAX_CSV_ROWS + 1)
            if len(frame) > MAX_CSV_ROWS:
                raise PrivateInputError('CSV_ROW_BUDGET_EXCEEDED')
            columns = ['Open', 'High', 'Low', 'Close']
            time_columns = [name for name in ('timestamp', 'Etc/UTC') if name in frame.columns]
            if len(time_columns) != 1 or not set(columns).issubset(frame.columns) or len(frame) < 2:
                raise ValueError
            stamps = [pd.Timestamp(value) for value in frame[time_columns[0]]]
            if any(pd.isna(stamp) or stamp.tzinfo is None
                   or stamp.utcoffset() != timedelta(0) for stamp in stamps):
                raise ValueError
            index = pd.DatetimeIndex(stamps).tz_convert('UTC')
            if index.has_duplicates or not index.is_monotonic_increasing:
                raise ValueError
            if any(pd.api.types.is_bool_dtype(frame[column]) for column in columns):
                raise ValueError
            frame = frame[columns].apply(pd.to_numeric, errors='raise')
            if not np.isfinite(frame.to_numpy(dtype=float)).all():
                raise ValueError
            frame.index = index
            frame.attrs['_private_source_path'] = str(path)
            frame.attrs['_private_config_sha256'] = config_sha256
            frame.attrs['_private_provenance'] = {
                'sha256': hashlib.sha256(raw).hexdigest(), 'rows': len(frame),
                'first_timestamp': index[0].isoformat(), 'last_timestamp': index[-1].isoformat(),
            }
            frames[pair] = frame
        except PrivateInputError:
            raise
        except (OSError, ValueError, TypeError, OverflowError, StopIteration, csv.Error, pd.errors.ParserError):
            raise PrivateInputError('INVALID_DATA') from None
    return dict(frames=frames, settings=config['settings'], profile=config['profile'],
                costs=config['costs'])


def validate_output_path(output_path):
    """Block public checkout files; allow ignored private dirs or external paths."""
    try:
        path = Path(output_path).expanduser().resolve()
        if path.name.lower() == 'report.json' or path.is_dir():
            raise ValueError
        if path.is_relative_to(REPOSITORY_ROOT):
            relative = path.relative_to(REPOSITORY_ROOT)
            if len(relative.parts) < 2 or relative.parts[0] not in ('private', 'backtest-results'):
                raise ValueError
        return path
    except (OSError, ValueError, TypeError):
        raise PrivateInputError('UNSAFE_OUTPUT_PATH') from None


def write_private_report(output_path, report):
    """Serialize first, then replace atomically, preserving a prior valid report."""
    path = validate_output_path(output_path)
    payload = json.dumps(report, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.research-', suffix='.tmp', delete=False) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        raise PrivateInputError('INVALID_ARGUMENTS')


def main(argv=None):
    parser = _SafeParser(prog='fx-research', description='Run a local private research backtest.')
    parser.add_argument('--config', required=True, metavar='PRIVATE_JSON')
    parser.add_argument('--output', required=True, metavar='PRIVATE_REPORT_JSON')
    code = 0
    public_status = {'status': 'HOLD', 'economic_accepted': False}
    try:
        args = parser.parse_args(argv)
        output = validate_output_path(args.output)
        config_path = Path(args.config).expanduser().resolve()
        # No upstream diagnostics or warning can print private values or paths.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            inputs = load_inputs(config_path)
            sources = {config_path}
            sources.update(Path(frame.attrs['_private_source_path']) for frame in inputs['frames'].values())
            if output in sources or (output.exists() and any(output.samefile(source) for source in sources)):
                raise PrivateInputError('UNSAFE_OUTPUT_PATH')
            result = run_backtest(**inputs)
            if not isinstance(result, dict):
                raise PrivateInputError('INVALID_RESEARCH_RESULT')
            result['economic_accepted'] = False
            result['new_orders_allowed'] = False
            result['provenance'] = {
                'config_sha256': next(iter(inputs['frames'].values())).attrs['_private_config_sha256'],
                'data': {pair: frame.attrs['_private_provenance'] for pair, frame in inputs['frames'].items()},
            }
            write_private_report(output, result)
    except PrivateInputError as error:
        public_status['error_code'] = str(error)
        code = 2
    except Exception:
        # Deliberately omit traceback/messages; caller can inspect private files locally.
        public_status['error_code'] = 'RESEARCH_FAILED'
        code = 2
    print(json.dumps(public_status, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
