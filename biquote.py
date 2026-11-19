"""Bounded public read-only biquote native OHLC adapter; never publishes raw bars.

Schema: https://biquote.io/docs/ (OHLC section), checked 2026-11-19 JST.
Terms: https://biquote.io/terms.html. Internal analytics/storage are permitted;
do not expose this returned raw feed as a public API or bulk download. Prices
are indicative and this adapter does not establish broker-price equivalence.
"""
from datetime import timedelta
import json
import math
import time

import pandas as pd
import requests


SYMBOLS = {'USD/JPY':'USDJPY','MXN/JPY':'MXNJPY','AUD/USD':'AUDUSD',
           'EUR/USD':'EURUSD','AUD/JPY':'AUDJPY'}
MINUTES = {'5m':5,'15m':15,'4h':240}
MAX_RESPONSE_BYTES = 1_000_000
BAR_LIMIT = 100


def _reject_constant(_value):
    raise ValueError('BIQUOTE_JSON_NONFINITE')


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('BIQUOTE_JSON_DUPLICATE_KEY')
        result[key]=value
    return result


def fetch_biquote(pair,label,now,*,get=requests.get,clock=None,sleep=time.sleep):
    """Bounded GET with one retry for a short upstream HTTP outage.

    Old closed bars remain old: freshness is still enforced by fx.indicators.
    No interpolation, forward fill, resampling or source-mixing occurs here.
    A live clock bounds provider timestamps separately from the report cutoff.
    """
    if pair not in SYMBOLS or label not in MINUTES:
        raise ValueError('BIQUOTE_UNSUPPORTED_REQUEST')
    now=pd.Timestamp(now)
    if pd.isna(now) or now.tzinfo is None:
        raise ValueError('BIQUOTE_NOW_TIMEZONE_REQUIRED')
    now=now.tz_convert('UTC')
    response=None
    try:
        for attempt in range(2):
            requested_at = now if clock is None else clock()
            response=get('https://biquote.io/api/'+SYMBOLS[pair]+'/ohlc',
                         params={'interval':label,'limit':BAR_LIMIT},
                         timeout=(5,20),stream=True,allow_redirects=False)
            if attempt != 0 or response.status_code not in (502, 503, 504):
                break
            retry_after = getattr(response, 'headers', {}).get('Retry-After', '1')
            if retry_after not in ('0', '1', '2'):
                break
            response.close()
            response = None
            sleep(int(retry_after))
        if response.status_code!=200:
            raise ValueError('BIQUOTE_HTTP_FAILURE')
        chunks=[]; total=0
        for chunk in response.iter_content(chunk_size=65536):
            total+=len(chunk)
            if total>MAX_RESPONSE_BYTES:
                raise ValueError('BIQUOTE_RESPONSE_BUDGET_EXCEEDED')
            chunks.append(chunk)
        payload=json.loads(b''.join(chunks),parse_constant=_reject_constant,object_pairs_hook=_unique)
        received_at = now if clock is None else clock()
    except ValueError:
        raise
    except Exception:
        # No transport/provider message or upstream body escapes this adapter.
        raise ValueError('BIQUOTE_FETCH_FAILED') from None
    finally:
        if response is not None: response.close()
    if not isinstance(payload,dict) or payload.get('symbol')!=SYMBOLS[pair] or payload.get('interval')!=label:
        raise ValueError('BIQUOTE_ENVELOPE_MISMATCH')
    bars=payload.get('bars')
    # The provider prepends one current candle to the requested closed-bar limit.
    if not isinstance(bars,list) or not 1<=len(bars)<=BAR_LIMIT+1:
        raise ValueError('BIQUOTE_BARS_INVALID')
    step=pd.Timedelta(minutes=MINUTES[label])
    timestamps=[]; closed_stamps=[]; values=[]; open_count=0
    for bar in bars:
        if not isinstance(bar,dict) or type(bar.get('isOpen')) is not bool or not isinstance(bar.get('openTime'),str):
            raise ValueError('BIQUOTE_BAR_SCHEMA_INVALID')
        try:
            stamp=pd.Timestamp(bar['openTime'])
        except (ValueError,TypeError,OverflowError):
            raise ValueError('BIQUOTE_TIMESTAMP_INVALID') from None
        if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset()!=timedelta(0) or stamp>received_at or stamp!=stamp.floor(step):
            raise ValueError('BIQUOTE_TIMESTAMP_INVALID')
        timestamps.append(stamp)
        prices=[bar.get(key) for key in ('open','high','low','close')]
        if any(type(value) not in (int,float) or not math.isfinite(value) for value in prices):
            raise ValueError('BIQUOTE_OHLC_INVALID')
        if not 0<prices[2]<=min(prices[0],prices[3])<=max(prices[0],prices[3])<=prices[1]:
            raise ValueError('BIQUOTE_OHLC_INVALID')
        if bar['isOpen']:
            open_count+=1
            if open_count>1 or not requested_at<stamp+step:
                raise ValueError('BIQUOTE_OPEN_FLAG_TIME_MISMATCH')
            continue
        if stamp+step>received_at:
            raise ValueError('BIQUOTE_CLOSED_FLAG_TIME_MISMATCH')
        closed_stamps.append(stamp); values.append(prices)
    order=pd.DatetimeIndex(timestamps)
    if order.has_duplicates or not (order.is_monotonic_increasing or order.is_monotonic_decreasing):
        raise ValueError('BIQUOTE_TIMESTAMP_ORDER_INVALID')
    if len(values) > BAR_LIMIT:
        raise ValueError('BIQUOTE_BARS_INVALID')
    if not values: raise ValueError('BIQUOTE_NO_CLOSED_BARS')
    frame=pd.DataFrame(values,index=pd.DatetimeIndex(closed_stamps),columns=['Open','High','Low','Close'],dtype=float)
    # Import lazily so fx may select this provider without an import cycle.
    from fx import validate_ohlc
    frame=validate_ohlc(frame,MINUTES[label],received_at)
    frame=frame.loc[frame.index + step <= now]
    if frame.empty:
        raise ValueError('BIQUOTE_NO_CLOSED_BARS')
    frame.attrs.update(provider='biquote',native_interval=label,price_side='mid',
                       raw_redistribution_permitted=False)
    return frame
