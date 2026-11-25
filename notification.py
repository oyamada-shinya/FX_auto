"""Pure consumer for public research reports. Never sends notifications or orders."""

from datetime import datetime, timezone
import math
import re


_PAIRS = ('USD/JPY', 'MXN/JPY', 'AUD/USD', 'EUR/USD', 'AUD/JPY')
_FRAMES = {'5m': 5, '15m': 15, '4h': 240}


class _Reject(ValueError):
    pass


def _timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _Reject('INVALID_TIMESTAMP')
    return value.astimezone(timezone.utc)


def _number(value, *, positive=False, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise _Reject('INVALID_INDICATOR')
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise _Reject('INVALID_INDICATOR')
    return value


def _semantically_equal(actual, expected, *components):
    """Allow float serialization/cancellation error, not inconsistent indicators."""
    if not math.isfinite(actual) or not math.isfinite(expected):
        return False
    # Price subtraction can lose precision when a band is very close to its
    # center. Scale the absolute floor by representable source-number spacing,
    # rather than by price itself, which could hide materially wrong widths.
    tolerance = max(1e-12, 8 * max(math.ulp(value) for value in components))
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=tolerance)


def _series(source, minutes, generated, now):
    if not isinstance(source, dict):
        raise _Reject('MISSING_SERIES')
    if any(key in source for key in ('error', 'error_type', 'errors', 'status')):
        raise _Reject('FAILED_SERIES')
    if source.get('fresh') is not True or source.get('quality_eligible') is not True:
        raise _Reject('SERIES_NOT_ELIGIBLE')
    closed = _timestamp(source['last_closed'])
    age = (now - closed).total_seconds() / 60
    if closed > generated or not 0 <= age <= minutes + 15:
        raise _Reject('CANDLE_EXPIRED_OR_FUTURE')
    result = {'fresh': True, 'quality_eligible': True,
              'last_closed': closed.isoformat(), 'age_minutes': age,
              'close': _number(source['close'], positive=True),
              'atr14': _number(source['atr14'], nonnegative=True),
              'recent_low': _number(source['recent_low'], positive=True),
              'recent_high': _number(source['recent_high'], positive=True)}
    if not result['recent_low'] <= result['close'] <= result['recent_high']:
        raise _Reject('INVALID_INDICATOR_RANGE')
    result['wma'] = {str(n): _number(source['wma'][str(n)], positive=True)
                     for n in (5, 25, 75)}
    bb = source['bb20']
    mean = _number(bb['mean'], positive=True)
    bands = {}
    for n in (1, 2, 3):
        values = bb['bands'][str(n)]
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise _Reject('INVALID_INDICATOR_RANGE')
        lower, upper = (_number(value) for value in values)
        if not lower <= mean <= upper:
            raise _Reject('INVALID_INDICATOR_RANGE')
        bands[str(n)] = [lower, upper]
    sigma = mean - bands['1'][0]
    for n in (1, 2, 3):
        lower, upper = bands[str(n)]
        # All bands must share a center and the same standard deviation. These
        # identities imply nested +/-n-sigma bands within floating tolerance.
        if (not _semantically_equal(mean-lower, n*sigma, mean, lower, upper)
                or not _semantically_equal(upper-mean, n*sigma, mean, lower, upper)):
            raise _Reject('INCONSISTENT_BB_BANDS')
    result['bb20'] = {'mean': mean, 'bands': bands}
    result['macd'] = {key: _number(source['macd'][key])
                      for key in ('line', 'signal', 'histogram')}
    macd = result['macd']
    if not _semantically_equal(macd['histogram'], macd['line']-macd['signal'],
                               macd['line'], macd['signal'], macd['histogram']):
        raise _Reject('INCONSISTENT_MACD')
    return result


def validate_notification(report, run, now, *, expected_commit=None, seen_event_keys=()):
    """Validate a report and return a whitelisted research-only notification payload.

    ``expected_commit`` must come from a separately verified repository ref, not
    from report text. ``run`` is GitHub's run metadata for that exact execution.
    Callers must obtain both over their trusted connector/API. This function does
    not authenticate arbitrary supplied dictionaries or prove artifact provenance.
    The caller owns durable deduplication state and updates it only after delivery.
    A valid payload still keeps HOLD: public analysis cannot authorize an order.
    """
    try:
        now = _timestamp(now)
        if not isinstance(report, dict) or not isinstance(run, dict):
            raise _Reject('INVALID_ENVELOPE')
        if (not isinstance(expected_commit, str)
                or re.fullmatch(r'[0-9a-f]{40}', expected_commit) is None):
            raise _Reject('MISSING_EXPECTED_COMMIT')
        if run.get('status') != 'completed' or run.get('conclusion') != 'success':
            raise _Reject('RUN_NOT_SUCCESSFUL')
        if run.get('head_sha') != expected_commit:
            raise _Reject('COMMIT_MISMATCH')
        if type(run.get('id')) is not int or run['id'] <= 0:
            raise _Reject('INVALID_RUN_ID')
        event_key = str(run['id']) + ':' + expected_commit
        if not isinstance(seen_event_keys, (set, frozenset, list, tuple)):
            raise _Reject('INVALID_DEDUPLICATION_STATE')
        if event_key in seen_event_keys:
            raise _Reject('DUPLICATE_EVENT')
        if type(report.get('schema_version')) is not int or report['schema_version'] != 2:
            raise _Reject('UNSUPPORTED_SCHEMA')
        if (report.get('mode') != 'research_only'
                or report.get('new_orders_allowed') is not False
                or report.get('decision') != 'HOLD'
                or report.get('acceptance') != 'NOT_EVALUATED'
                or report.get('data_status') != 'PASS'):
            raise _Reject('UNSAFE_OR_FAILED_REPORT')
        generated = _timestamp(report['generated_at'])
        published = _timestamp(report['published_at'])
        if not generated <= published <= now:
            raise _Reject('REPORT_TIME_ORDER_INVALID')
        if (now - generated).total_seconds() > 90 * 60:
            raise _Reject('REPORT_EXPIRED')
        pairs = report['pairs']
        if not isinstance(pairs, dict) or set(pairs) != set(_PAIRS):
            raise _Reject('INCOMPLETE_PAIR_SET')
        safe_pairs = {}
        for pair in _PAIRS:
            frames = pairs[pair]
            if not isinstance(frames, dict) or set(frames) != set(_FRAMES):
                raise _Reject('INCOMPLETE_TIMEFRAME_SET')
            safe_pairs[pair] = {label: _series(frames[label], minutes, generated, now)
                                for label, minutes in _FRAMES.items()}
        return {'accepted': True, 'status': 'VALIDATED_RESEARCH_ONLY',
                'decision': 'HOLD', 'new_orders_allowed': False,
                'acceptance': 'NOT_EVALUATED', 'schema_version': 2,
                'event_key': event_key, 'commit': expected_commit,
                'generated_at': generated.isoformat(), 'published_at': published.isoformat(),
                'validated_at': now.isoformat(), 'pairs': safe_pairs}
    except _Reject as error:
        reason = error.args[0]
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        # Never echo provider errors, malformed content, or unknown extra fields.
        reason = 'MALFORMED_REPORT'
    return {'accepted': False, 'status': 'HOLD', 'decision': 'HOLD',
            'new_orders_allowed': False, 'reasons': [reason]}
