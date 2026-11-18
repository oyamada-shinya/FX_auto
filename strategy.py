"""Finite research hypotheses and chronological walk-forward orchestration.

No generated candidate permits orders or establishes profitability. Search-space
and account inputs belong to external private configuration, never defaults.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from itertools import product
import math

from backtest import _frames, run_backtest, select_training_candidate
from risk import PAIR_RULES, validate_setting


SEARCH_KEYS = ('range_atr','spacing_atr','profit_atr','stop_atr','quantities','position_caps')
TIMEFRAMES = {'5m':5,'15m':15,'4h':240}


def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def _space(search_space):
    if not isinstance(search_space,dict) or not all(key in search_space for key in (*SEARCH_KEYS,'squeeze_ratio')):
        raise ValueError('All finite external search-space lists and squeeze_ratio are required')
    combinations = 1
    for key in SEARCH_KEYS:
        values = search_space[key]
        if not isinstance(values,(list,tuple)) or not 1<=len(values)<=100:
            raise ValueError('Search lists must be finite nonempty and bounded')
        if any(not _finite(value) or value<=0 for value in values):
            raise ValueError('Search values must be finite and positive')
        if key in ('quantities','position_caps') and any(value!=int(value) for value in values):
            raise ValueError('External quantities and position caps must be integers')
        combinations *= len(values)
    if combinations>100:
        raise ValueError('Search exceeds the finite 100-candidate budget')
    if not _finite(search_space['squeeze_ratio']) or not 0<search_space['squeeze_ratio']<1:
        raise ValueError('Explicit relative BB squeeze threshold must be inside (0,1)')


def _direction(item, minutes, squeeze_ratio):
    if not isinstance(item,dict) or item.get('fresh') is not True or item.get('quality_eligible') is not True or item.get('valid',True) is not True or 'error_code' in item or 'error' in item:
        return None,'FRAME_QUALITY_OR_FRESHNESS_FAILURE'
    try:
        close, atr, age = item['close'],item['atr14'],item['age_minutes']
        means = [item['wma'][str(period)] for period in (5,25,75)]
        line, signal, histogram = (item['macd'][key] for key in ('line','signal','histogram'))
        bb_mean = item['bb20']['mean']
        bands = [item['bb20']['bands'][str(n)] for n in (1,2,3)]
        values = [close,atr,age,bb_mean,*means,line,signal,histogram,*[value for band in bands for value in band]]
        if not all(_finite(value) for value in values): raise ValueError()
        if min(close,atr,bb_mean,*means)<=0 or not 0<=age<=minutes+15: raise ValueError()
        if not math.isclose(histogram,line-signal,rel_tol=1e-9,abs_tol=1e-12): raise ValueError()
        if any(len(band)!=2 or not 0<band[0]<=bb_mean<=band[1] for band in bands): raise ValueError()
        if not bands[2][0]<=bands[1][0]<=bands[0][0] or not bands[0][1]<=bands[1][1]<=bands[2][1]: raise ValueError()
    except (KeyError,TypeError,ValueError,IndexError):
        return None,'FRAME_INDICATORS_INVALID'
    if (bands[1][1]-bands[1][0])/bb_mean <= squeeze_ratio:
        return None,'BB_SQUEEZE_HYPOTHESIS_HOLD'
    if means[0]>means[1]>means[2] and line>0 and histogram>0:
        return 'buy',None
    if means[0]<means[1]<means[2] and line<0 and histogram<0:
        return 'sell',None
    return None,'FLAT_OR_DIRECTION_DISAGREEMENT'


def _round(value, step, rounding):
    value,step = Decimal(str(value)),Decimal(str(step))
    return float((value/step).to_integral_value(rounding=rounding)*step)


def generate_candidates(pair, frames_report, search_space):
    """Create bounded candidates from a current *research* indicator snapshot.

    Input matches fx.indicators pair-level {5m,15m,4h}. Each candidate includes
    explicit broker-pip widths and a price-unit adapter for run_backtest.
    Do not use a present-day report to derive past walk-forward candidates.
    """
    _space(search_space)
    result = dict(candidates=[],reasons=[],decision='HOLD',new_orders_allowed=False,
                  economic_accepted=False, hypothesis='UNVALIDATED_MULTI_TIMEFRAME_TREND',
                  historical_use='Generate each candidate family from training data only')
    if pair not in PAIR_RULES or not isinstance(frames_report,dict):
        result['reasons']=['UNSUPPORTED_PAIR_OR_REPORT']; return result
    directions=[]
    for name,minutes in TIMEFRAMES.items():
        direction,reason=_direction(frames_report.get(name),minutes,search_space['squeeze_ratio'])
        if reason: result['reasons'].append(name+':'+reason)
        directions.append(direction)
    if result['reasons']: return result
    if len(set(directions))!=1:
        result['reasons']=['TIMEFRAME_DIRECTION_DISAGREEMENT']; return result
    side=directions[0]
    tick,pip,minimum = map(float,PAIR_RULES[pair])
    anchor = frames_report['5m']['close']
    atr = frames_report['15m']['atr14']
    stop_atr = frames_report['4h']['atr14']
    bb_lower,bb_upper = frames_report['15m']['bb20']['bands']['2']
    seen=set()
    for range_multiple,spacing_multiple,profit_multiple,stop_multiple,quantity,cap in product(*(search_space[key] for key in SEARCH_KEYS)):
        lower = _round(max(bb_lower,anchor-range_multiple*atr),tick,ROUND_FLOOR)
        upper = _round(min(bb_upper,anchor+range_multiple*atr),tick,ROUND_CEILING)
        spacing_pips = _round(max(minimum,spacing_multiple*atr/pip),.1,ROUND_CEILING)
        profit_pips = _round(max(minimum,profit_multiple*atr/pip),.1,ROUND_CEILING)
        stop = _round(lower-stop_multiple*stop_atr if side=='buy' else upper+stop_multiple*stop_atr,
                      tick,ROUND_FLOOR if side=='buy' else ROUND_CEILING)
        try:
            validated=validate_setting(pair,side,lower,upper,spacing_pips,profit_pips,int(quantity),stop,int(cap))
        except ValueError:
            continue
        setting=dict(direction=side,lower=lower,upper=upper,spacing=validated['spacing_price'],
                     take_profit=validated['profit_price'],stop=stop,quantity=int(quantity),max_positions=int(cap))
        identity=tuple(setting.items())
        if identity in seen: continue
        seen.add(identity)
        result['candidates'].append(dict(backtest_settings={pair:setting},broker_validation=validated,
                                          broker_widths=dict(spacing_pips=spacing_pips,profit_pips=profit_pips)))
    result['reasons']=['UNVALIDATED_STRATEGY_HYPOTHESIS','PRIVATE_PORTFOLIO_AND_HISTORY_ACCEPTANCE_REQUIRED']
    if not result['candidates']: result['reasons'].append('NO_BROKER_VALID_BOUNDED_CANDIDATE')
    return result


def walk_forward(frames,candidates,profile,costs,train_bars,test_bars):
    """Rolling fixed-size training, disjoint chronological OOS windows.

    Fold equity is independently initialized; fold returns are never presented
    as a continuous portfolio. Every run obeys backtest's historical gates.
    Caller-specified static candidate provenance is not independently verified.
    """
    for value in (train_bars,test_bars):
        if isinstance(value,bool) or not isinstance(value,int) or value<2:
            raise ValueError('Training and test lengths must be explicit integers >=2')
    frames,index=_frames(frames)
    result=dict(status='HOLD',economic_accepted=False,folds=[],
                reasons=['CANDIDATE_TRAINING_PROVENANCE_UNVERIFIED','BROKER_AND_ECONOMIC_ACCEPTANCE_REQUIRED'],
                methodology='Fixed rolling training; disjoint OOS; independent fold capital, no compounding')
    if len(index)<train_bars+test_bars:
        result['reasons'].append('INSUFFICIENT_WALK_FORWARD_HISTORY'); return result
    for start in range(train_bars,len(index)-test_bars+1,test_bars):
        train={pair:frame.iloc[start-train_bars:start].copy() for pair,frame in frames.items()}
        test={pair:frame.iloc[start:start+test_bars].copy() for pair,frame in frames.items()}
        selection=select_training_candidate(train,candidates,profile,costs)
        fold=dict(train_start=index[start-train_bars].isoformat(),train_end=index[start-1].isoformat(),
                  test_start=index[start].isoformat(),test_end=index[start+test_bars-1].isoformat(),
                  selection=selection,test_result=None,status='HOLD')
        if selection['candidate_index'] is not None:
            evaluation=run_backtest(test,candidates[selection['candidate_index']],profile,costs)
            fold['test_result']=evaluation
            if evaluation['metrics'] is not None: fold['status']='RESEARCH_ONLY'
        result['folds'].append(fold)
    if all(fold['status']=='RESEARCH_ONLY' for fold in result['folds']): result['status']='RESEARCH_ONLY'
    else: result['reasons'].append('ONE_OR_MORE_FOLDS_BLOCKED_BY_HISTORY_OR_RISK_GATE')
    if profile.get('mode','historical')=='scenario': result['reasons'].append('SCENARIO_ONLY_NOT_HISTORICAL_ACCEPTANCE')
    return result
