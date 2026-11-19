import json
import hashlib
from importlib.metadata import version
import time
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

PAIRS = {'USD/JPY':'JPY=X','MXN/JPY':'MXNJPY=X','AUD/USD':'AUDUSD=X','EUR/USD':'EURUSD=X','AUD/JPY':'AUDJPY=X'}
SAFE_ERRORS = {
    'Timezone-aware timestamps required': 'TIMEZONE_INVALID',
    'Missing OHLC columns': 'SCHEMA_INVALID',
    'Duplicate timestamps': 'TIMESTAMPS_DUPLICATED',
    'Future provider timestamps': 'TIMESTAMPS_FUTURE',
    'No closed candles': 'NO_CLOSED_BARS',
    'Misaligned candle timestamps': 'TIMESTAMPS_OFF_GRID',
    'Non-finite OHLC': 'OHLC_NONFINITE',
    'Invalid OHLC': 'OHLC_INVALID',
    'Insufficient candles': 'HISTORY_INSUFFICIENT',
    'Intraday candle gap': 'INTRADAY_GAP',
    'Empty provider response': 'PROVIDER_EMPTY',
    'Non-finite indicators': 'INDICATOR_NONFINITE',
    'Boolean OHLC': 'OHLC_BOOLEAN',
}

def validate_ohlc(df, minutes, now):
    """Closed UTC bars only. Never silently repair provider corruption."""
    now = pd.Timestamp(now)
    if (not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None
            or now.tzinfo is None or df.index.hasnans):
        raise ValueError('Timezone-aware timestamps required')
    if not isinstance(minutes, int) or minutes <= 0:
        raise ValueError('Positive integer candle duration required')
    if not set(('Open', 'High', 'Low', 'Close')).issubset(df.columns):
        raise ValueError('Missing OHLC columns')
    if df.index.has_duplicates:
        raise ValueError('Duplicate timestamps')
    df = df.sort_index().copy()
    df.index = df.index.tz_convert('UTC')
    step = pd.Timedelta(minutes=minutes)
    if (df.index > now).any():
        raise ValueError('Future provider timestamps')
    df = df[df.index + step <= now]
    if df.empty:
        raise ValueError('No closed candles')
    if not (df.index == df.index.floor(step)).all():
        raise ValueError('Misaligned candle timestamps')
    if any(isinstance(value, (bool, np.bool_))
           for value in df[['Open','High','Low','Close']].to_numpy().ravel()):
        raise ValueError('Boolean OHLC')
    if not np.isfinite(df[['Open', 'High', 'Low', 'Close']].to_numpy(dtype=float)).all():
        raise ValueError('Non-finite OHLC')
    if ((df.High < df[['Open','Close','Low']].max(axis=1)) | (df.Low > df[['Open','Close','High']].min(axis=1)) | (df.Low <= 0)).any():
        raise ValueError('Invalid OHLC')
    return df


def indicators(df, minutes, now):
    df = validate_ohlc(df, minutes, now)
    if len(df) < 75:
        raise ValueError('Insufficient candles')
    # Cross-date gaps are retained and disclosed (weekends/holidays are not imputed).
    # Any intraday hole is a data-quality failure until its session is verified.
    stamps = df.index.to_series()
    holes = stamps.diff() > pd.Timedelta(minutes=minutes)
    intraday = stamps.dt.normalize() == stamps.shift().dt.normalize()
    if (holes & intraday).any():
        raise ValueError('Intraday candle gap')
    c = df.Close
    age = (now - df.index[-1] - pd.Timedelta(minutes=minutes)).total_seconds()/60
    tr = pd.concat([df.High-df.Low,(df.High-c.shift()).abs(),(df.Low-c.shift()).abs()],axis=1).max(axis=1)
    atr = tr.iloc[:14].mean()
    for v in tr.iloc[14:]:
        atr = (atr*13+v)/14
    mean = c.iloc[-20:].mean()
    sd = c.iloc[-20:].std(ddof=0)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    result = {
        'candles': len(df),
        'cross_date_gaps': int((holes & ~intraday).sum()),
        'quality_eligible': not bool(holes.any()) and not df.attrs.get('incomplete_closed_bins', 0),
        'incomplete_closed_bins': int(df.attrs.get('incomplete_closed_bins', 0)),
        'last_closed': (df.index[-1]+pd.Timedelta(minutes=minutes)).isoformat(),
        'age_minutes': age, 'fresh': 0 <= age <= minutes+15,
        'close': float(c.iloc[-1]),
        'wma': {str(n): float(sum(v*w for w,v in enumerate(c.iloc[-n:],1))/(n*(n+1)/2))
                for n in (5,25,75)},
        'atr14': float(atr),
        'bb20': {'mean': float(mean), 'bands': {
            str(n): [float(mean-n*sd), float(mean+n*sd)] for n in (1,2,3)}},
        'macd': {'line': float(macd.iloc[-1]), 'signal': float(signal.iloc[-1]),
                 'histogram': float((macd-signal).iloc[-1])},
        'recent_low': float(df.Low.iloc[-20:].min()),
        'recent_high': float(df.High.iloc[-20:].max()),
    }
    try:
        json.dumps(result, allow_nan=False)
    except ValueError:
        raise ValueError('Non-finite indicators') from None
    return result


def four_hour_bars(df, now):
    df = validate_ohlc(df, 60, now)
    grouped = df.resample('4h', origin='start_day', closed='left', label='left')
    result = grouped.agg({'Open':'first','High':'max','Low':'min','Close':'last'})
    # Unique, aligned hourly timestamps were checked before counting.
    complete = grouped.Close.count() == 4
    closed = result.index + pd.Timedelta(hours=4) <= now
    incomplete = ~complete & closed
    first_bucket = result.index[0]
    leading_slice = df.index[0] != first_bucket
    if leading_slice:
        incomplete.iloc[0] = False
    bars = result[complete & closed].copy()
    expected_close = pd.Timestamp(now).tz_convert('UTC').floor('4h')
    last_input_bin_close = df.index[-1].floor('4h') + pd.Timedelta(hours=4)
    trailing_missing = max(0, int((expected_close-last_input_bin_close)//pd.Timedelta(hours=4)))
    bars.attrs['incomplete_closed_bins'] = int(incomplete.sum()) + trailing_missing
    bars.attrs['leading_partial_bin'] = bool(leading_slice)
    return bars


def fetch_history(symbol, interval, period, attempts=3, sleep=time.sleep, ticker=yf.Ticker):
    if not isinstance(attempts, int) or not 1 <= attempts <= 3:
        raise ValueError('Attempts must be between one and three')
    for attempt in range(attempts):
        try:
            df = ticker(symbol).history(period=period, interval=interval,
                                        auto_adjust=False, raise_errors=True, timeout=20, keepna=True)
            if df.empty:
                raise ValueError('Empty provider response')
            df.attrs['fetch_attempts'] = attempt + 1
            return df
        except Exception:
            if attempt + 1 == attempts:
                raise
            sleep(2 ** attempt)


def write_report(report, path='report.json'):
    serialized = json.dumps(report, indent=2, allow_nan=False)
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(serialized, encoding='utf-8')
    temporary.replace(path)

def quote_gaps(df, minutes, now):
    """Summarize invalid closed quotes without changing data eligibility."""
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        return {}
    step = pd.Timedelta(minutes=minutes)
    closed = df.loc[df.index + step <= now, ['Open', 'High', 'Low', 'Close']].sort_index()
    invalid = ~np.isfinite(closed.to_numpy(dtype=float)).all(axis=1)
    bad = closed.index[invalid].tz_convert('UTC')
    ny = bad.tz_convert('America/New_York')
    # Reference window for diagnosis only. It is not a provider or broker calendar.
    weekend = ((ny.dayofweek == 4) & (ny.hour >= 17)) | (ny.dayofweek == 5) | (
        (ny.dayofweek == 6) & (ny.hour < 17))
    runs = []
    if len(bad):
        first = last = bad[0]
        for timestamp in bad[1:]:
            if timestamp - last != step:
                runs.append((first, last))
                first = timestamp
            last = timestamp
        runs.append((first, last))
    all_missing = int(closed.isna().all(axis=1).sum())
    return {
        'closed_all_missing_rows': all_missing,
        'closed_other_nonfinite_rows': len(bad) - all_missing,
        'closed_nonfinite_rows_in_regular_weekend_window': int(weekend.sum()),
        'closed_nonfinite_rows_outside_regular_weekend_window': int((~weekend).sum()),
        'recent_closed_nonfinite_runs': [
            {'first_open': first.isoformat(), 'last_open': last.isoformat()}
            for first, last in runs[-8:]
        ],
    }


def secondary_four_hour(frame, now):
    """Compute 4h research indicators without publishing the reference feed."""
    candles = validate_ohlc(frame, 240, now)
    duration = pd.Timedelta(hours=4)
    last_closed = candles.index[-1] + duration
    details = {'provider': 'biquote', 'last_closed': last_closed.isoformat(),
               'closed_candles': len(candles)}
    if last_closed != pd.Timestamp(now).tz_convert('UTC').floor('4h'):
        return {**details, 'status': 'MISSING_LATEST_CLOSED_BAR'}
    for left, right in zip(candles.index[:-1], candles.index[1:]):
        if right - left == duration:
            continue
        start = (left + duration).tz_convert('America/New_York')
        end = right.tz_convert('America/New_York')
        starts_in_weekend = (start.dayofweek == 4 and start.hour >= 17
                             or start.dayofweek in (5, 6))
        ends_in_weekend = (end.dayofweek in (4, 5)
                           or end.dayofweek == 6 and (end.hour, end.minute) <= (17, 0))
        if not (starts_in_weekend and ends_in_weekend
                and start.isocalendar()[:2] == end.isocalendar()[:2]):
            return {**details, 'status': 'UNEXPLAINED_BAR_GAP'}
    values = indicators(candles, 240, now)
    return {**details, 'status': 'REFERENCE_ONLY', 'weekend_calendar': 'NY_REFERENCE',
            **{key: values[key] for key in ('wma', 'atr14', 'bb20', 'macd')}}


def main(now=None, fetcher=fetch_history, output_path='report.json', secondary_fetcher=None):
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    report = {'schema_version':2,'generated_at':now.isoformat(),'mode':'research_only',
              'new_orders_allowed':False,'decision':'HOLD',
              'acceptance':'NOT_EVALUATED','pairs':{},
              'runtime_versions':{name:version(name) for name in ('pandas','numpy','yfinance')}}
    failed = False
    for pair,symbol in PAIRS.items():
        result = {}
        report['pairs'][pair] = result
        for label,interval,period,minutes in [('5m','5m','5d',5),('15m','15m','10d',15),('4h','1h','60d',240)]:
            diagnostics = {}
            try:
                df = fetcher(symbol, interval, period)
                diagnostics = {
                    'received_candles': len(df),
                    'duplicate_timestamps': int(df.index.duplicated().sum()),
                    'nonfinite_ohlc_rows': int((~np.isfinite(df[['Open','High','Low','Close']].to_numpy(dtype=float))).any(axis=1).sum()),
                    'provider': 'Yahoo Finance via yfinance', 'symbol': symbol,
                    'interval': interval, 'requested_period': period,
                    'observed_first': df.index[0].isoformat() if len(df) else None,
                    'observed_last': df.index[-1].isoformat() if len(df) else None,
                    'normalized_ohlc_sha256': hashlib.sha256(
                        df[['Open','High','Low','Close']].to_csv().encode('utf-8')).hexdigest(),
                    'fetch_attempts': df.attrs.get('fetch_attempts'),
                }
                diagnostics.update(quote_gaps(df, 60 if label == '4h' else minutes, now))
                if label == '4h':
                    df = four_hour_bars(df, now)
                result[label] = indicators(df,minutes,now)
                result[label]['source_diagnostics'] = diagnostics
                if not result[label]['fresh'] or not result[label]['quality_eligible']: failed = True
            except Exception as e:
                # Provider exception messages may contain URLs or tokens; publish category only.
                result[label] = {'error_type':type(e).__name__,
                                 'error_code':SAFE_ERRORS.get(str(e), 'PROVIDER_FAILURE'),
                                 'status':'DATA_UNAVAILABLE','source_diagnostics':diagnostics}
                failed = True
        primary_four_hour = result['4h']
        if secondary_fetcher is not None and not (
                primary_four_hour.get('fresh') and primary_four_hour.get('quality_eligible')):
            try:
                secondary_frame = secondary_fetcher(pair, '4h', now)
                report.setdefault('secondary_4h', {})[pair] = secondary_four_hour(
                    secondary_frame, now)
            except Exception as error:
                report.setdefault('secondary_4h', {})[pair] = {
                    'status': 'UNAVAILABLE', 'error_type': type(error).__name__}
    report['data_status'] = 'FAIL' if failed else 'PASS'
    report['market_status'] = 'UNKNOWN_CALENDAR'
    report['published_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    report['stop_reasons'] = ['HISTORICAL_ACCEPTANCE_MISSING', 'PRIVATE_RISK_NOT_EVALUATED',
                              'BROKER_CALENDAR_NOT_VERIFIED']
    if failed:
        report['stop_reasons'].append('DATA_QUALITY_OR_FRESHNESS_FAILURE')
    write_report(report, output_path)
    print(json.dumps(report,indent=2))
    return int(failed)

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--secondary-4h', action='store_true',
                        help='Include alternate derived 4h research indicators')
    args = parser.parse_args()
    if args.secondary_4h:
        from biquote import fetch_biquote
        raise SystemExit(main(secondary_fetcher=fetch_biquote))
    raise SystemExit(main())
