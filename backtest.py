"""Conservative fixed-grid research simulator. No broker or order API exists.

Costs are PRICE units except signed swap (quote currency/unit/UTC day).
Private account/risk inputs are mandatory and must not be published with reports.
This simulator is not a certification of broker execution or economic acceptance.
"""
import math
import statistics

import numpy as np
import pandas as pd
from risk import evaluate_portfolio


PROFILE_KEYS = ('initial_cash', 'max_floating_loss', 'max_drawdown',
                'min_maintenance_pct', 'max_positions')
SETTING_KEYS = ('lower', 'upper', 'spacing', 'take_profit', 'stop', 'quantity', 'max_positions')
COST_KEYS = ('spread', 'slippage', 'swap_long', 'swap_short', 'margin_rate')
SUPPORTED = {'USD/JPY', 'MXN/JPY', 'AUD/USD', 'EUR/USD', 'AUD/JPY'}
# Research resource envelopes, not broker limits or private risk settings.
MAX_ENGINE_POSITIONS = 1000
MAX_WORK_UNITS = 20_000_000


def _numbers(record, required, label):
    if not isinstance(record, dict) or any(key not in record for key in required):
        raise ValueError(label + ': missing required configuration')
    for key in required:
        if isinstance(record[key], bool) or not isinstance(record[key], (float, int)) or not math.isfinite(record[key]):
            raise ValueError(label + ': finite numeric configuration required')


def _frames(frames):
    if not isinstance(frames, dict) or not frames:
        raise ValueError('Nonempty synchronized OHLC frames required')
    index = None
    result = {}
    for pair, frame in frames.items():
        if pair not in SUPPORTED or not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError('Unsupported pair or timestamp index')
        if frame.index.tz is None or frame.index.hasnans or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            raise ValueError('Unique increasing timezone-aware timestamps required')
        if len(frame) < 2 or not {'Open', 'High', 'Low', 'Close'}.issubset(frame.columns):
            raise ValueError('OHLC history required')
        frame = frame[['Open','High','Low','Close']].copy().tz_convert('UTC')
        for column in frame:
            values = frame[column]
            if pd.api.types.is_bool_dtype(values.dtype):
                raise ValueError('Boolean OHLC is not a market price')
            if values.dtype == object and values.map(lambda value: isinstance(value,(bool,np.bool_)) or not isinstance(value,(int,float,np.number))).any():
                raise ValueError('Numeric non-boolean OHLC required')
        try:
            finite = np.isfinite(frame.to_numpy(dtype=float)).all()
        except (TypeError, ValueError):
            raise ValueError('Numeric OHLC required') from None
        if not finite or (frame <= 0).any().any() or (frame.High < frame[['Open','Close','Low']].max(axis=1)).any() or (frame.Low > frame[['Open','Close','High']].min(axis=1)).any():
            raise ValueError('Invalid OHLC')
        frame = frame.astype(float)
        if index is not None and not frame.index.equals(index):
            raise ValueError('All trading and conversion timestamps must be synchronized')
        index = frame.index
        result[pair] = frame
    return result, index


def _configuration(settings, profile, costs, frames):
    _numbers(profile, PROFILE_KEYS, 'profile')
    if any(profile[key] <= 0 for key in PROFILE_KEYS):
        raise ValueError('Positive private account and risk limits required')
    if profile['max_positions'] != int(profile['max_positions']):
        raise ValueError('Integer portfolio position cap required')
    if profile['max_positions']>MAX_ENGINE_POSITIONS:
        raise ValueError('Unsupported research engine position envelope')
    if profile.get('mode', 'historical') not in ('historical', 'scenario'):
        raise ValueError('Unknown evaluation mode')
    if not isinstance(settings, dict) or not settings or not isinstance(costs, dict):
        raise ValueError('Explicit candidate settings and costs required')
    levels = {}
    for pair, setting in settings.items():
        if pair not in frames or pair not in SUPPORTED or pair not in costs:
            raise ValueError('Missing pair history or costs')
        _numbers(setting, SETTING_KEYS, 'candidate')
        _numbers(costs[pair], COST_KEYS, 'costs')
        if setting.get('direction') not in ('buy','sell'):
            raise ValueError('Candidate direction must be buy or sell')
        if min(setting['lower'], setting['upper'], setting['spacing'], setting['take_profit'], setting['stop'], setting['quantity'], setting['max_positions']) <= 0 or setting['lower'] > setting['upper']:
            raise ValueError('Invalid bounded grid')
        if setting['quantity'] != int(setting['quantity']) or setting['max_positions'] != int(setting['max_positions']):
            raise ValueError('Integer quantity and candidate position cap required')
        if setting['max_positions']>MAX_ENGINE_POSITIONS:
            raise ValueError('Unsupported research engine position envelope')
        if setting['direction'] == 'buy' and setting['stop'] >= setting['lower'] or setting['direction'] == 'sell' and setting['stop'] <= setting['upper']:
            raise ValueError('Stop must be outside the grid on the loss side')
        if costs[pair]['spread'] < 0 or costs[pair]['slippage'] < 0 or not 0 < costs[pair]['margin_rate'] <= 1:
            raise ValueError('Invalid cost or margin assumptions')
        count = math.floor((setting['upper']-setting['lower'])/setting['spacing']+1e-10)+1
        if count > MAX_ENGINE_POSITIONS:
            raise ValueError('Candidate grid exceeds research engine bound')
        levels[pair] = [setting['lower'] + n*setting['spacing'] for n in range(count)]
        if pair.endswith('/USD') and 'USD/JPY' not in frames:
            raise ValueError('Synchronized USD/JPY conversion is required')
    return levels


def run_backtest(frames, settings, profile, costs):
    """Simulate externally specified static repeat-order grids on aligned bars.

    New-entry TP is deferred to the following bar. Stops are adverse-first;
    simultaneous portfolio stress uses all adverse bar extremes. Risk liquidation
    is modeled at those extremes with costs, not at a guaranteed stop price.
    """
    return _run_backtest(frames, settings, profile, costs)


def _run_backtest(frames, settings, profile, costs, *, verified_fold=False):
    # A fold may be short or span a closure only after the entire history was
    # matched against the independently supplied session grid.
    frames, index = _frames(frames)
    levels = _configuration(settings, profile, costs, frames)
    mode = profile.get('mode', 'historical')
    span = (index[-1]-index[0]).total_seconds()/86400
    blockers = ['broker_execution_not_certified', 'event_regimes_not_validated',
                'historical_costs_and_swap_calendar_not_verified', 'walk_forward_not_executed']
    if mode == 'historical' and not verified_fold and span < 90:
        return dict(status='HOLD', economic_accepted=False, reasons=['history_below_90_days'],
                    metrics=None, history_days=span, mode=mode)
    cadence = index.to_series().diff().dropna()
    if (mode == 'historical' and not verified_fold
            and (cadence.max() > pd.Timedelta(hours=4) or not (cadence == cadence.iloc[0]).all())):
        return dict(status='HOLD', economic_accepted=False,
                    reasons=['historical_cadence_or_coverage_unverified'],metrics=None,
                    history_days=span,mode=mode)
    if mode == 'scenario': blockers.insert(0, 'synthetic_or_short_scenario_not_economic_acceptance')
    cash = float(profile['initial_cash'])
    peak = cash
    positions, trades, ledger = [], [], []
    max_loss = max_dd = max_margin = 0.0
    min_ratio = None
    max_count = 0
    max_pending_margin = 0.0
    stopped = False
    stopped_pairs = set()
    stop_reasons = []
    swap_total = 0.0
    spread_slippage_total = 0.0
    # Accept a finite deterministic ladder before simulating. Unaccepted levels
    # are not hypothetical pending orders and cannot fill later in the run.
    slots = int(profile['max_positions'])
    for pair in sorted(levels):
        levels[pair] = levels[pair][:min(int(settings[pair]['max_positions']),slots)]
        slots -= len(levels[pair])
    accepted_levels = sum(len(pair_levels) for pair_levels in levels.values())
    # Nested pending-level/position scans are quadratic in the accepted ladder.
    # This is a deterministic workload envelope, not an elapsed-time guarantee.
    work_units = len(index)*max(1,accepted_levels)*(1+accepted_levels)
    if work_units>MAX_WORK_UNITS:
        raise ValueError('Simulation exceeds research work budget')

    def conversion(pair, i, field='Close'):
        return float(frames['USD/JPY'].iloc[i][field]) if pair.endswith('/USD') else 1.0

    def friction(pair):
        return costs[pair]['spread']/2 + costs[pair]['slippage']

    def mark(i, kind='Close'):
        floating = margin = 0.0
        for position in positions:
            pair = position['pair']; side = position['side']
            field = ('Low' if side == 1 else 'High') if kind == 'adverse' else ('High' if side == 1 else 'Low') if kind == 'favorable' else kind
            price = float(frames[pair].iloc[i][field])
            rate = conversion(pair, i, field if kind in ('adverse','favorable') else kind)
            # USD conversion direction must also be adverse to a losing P/L.
            quote = (price-position['entry'])*side*position['units'] - friction(pair)*position['units']
            if pair.endswith('/USD') and kind in ('adverse','favorable'):
                usd = frames['USD/JPY'].iloc[i]
                rate = float(usd.High if (quote < 0) == (kind == 'adverse') else usd.Low)
            floating += quote*rate
            margin_field = 'Open' if kind=='Open' else 'High' if kind in ('adverse','favorable') else 'Close'
            margin += price*position['units']*costs[pair]['margin_rate']*conversion(pair,i,margin_field)
        return floating, margin

    def close(position, price, i, reason, at_open=False):
        nonlocal cash, spread_slippage_total
        pair = position['pair']
        exit_price = price-position['side']*friction(pair)
        quote_pnl = (exit_price-position['entry'])*position['side']*position['units']
        rate = conversion(pair,i,'Open' if at_open else 'High' if quote_pnl<0 else 'Low')
        pnl = quote_pnl*rate
        cash += pnl
        spread_slippage_total += friction(pair)*position['units']*conversion(pair,i)
        trades.append(dict(pnl=pnl+position['swap'], reason=reason, pair=pair,
                           holding_hours=(index[i]-position['opened']).total_seconds()/3600))
        positions.remove(position)

    def pending_reserve(i,kind):
        reserve = 0.0
        field = 'Open' if kind=='Open' else 'High'
        for pair,pair_levels in levels.items():
            if pair in stopped_pairs: continue
            for level in pair_levels:
                if any(p['pair']==pair and abs(p['level']-level)<1e-10 for p in positions): continue
                reserve += level*settings[pair]['quantity']*costs[pair]['margin_rate']*conversion(pair,i,field)
        return reserve

    def risk_snapshot(i, kind):
        nonlocal peak, max_loss, max_dd, max_margin, min_ratio, max_pending_margin
        floating, margin = mark(i,kind)
        equity = cash+floating
        peak = max(peak,equity)
        max_loss = max(max_loss,-floating)
        max_dd = max(max_dd,peak-equity)
        max_margin = max(max_margin,margin)
        pending = pending_reserve(i,kind)
        max_pending_margin = max(max_pending_margin,pending)
        snapshot = evaluate_portfolio(equity,floating,peak,margin,pending,
                                     [dict(pair=p['pair'],side='buy' if p['side']==1 else 'sell',units=p['units']) for p in positions],profile)
        ratio = snapshot['maintenance_pct']
        if ratio is not None: min_ratio = ratio if min_ratio is None else min(min_ratio,ratio)
        # Reaching the inventory cap blocks additions; it does not require
        # forced liquidation of otherwise healthy open positions.
        reasons = [reason for reason in snapshot['stop_reasons'] if reason!='POSITION_LIMIT']
        return reasons, equity

    for i in range(1,len(index)):
        if stopped:
            ledger.append((index[i],cash))
            continue
        days = (index[i].date()-index[i-1].date()).days
        for position in list(positions):
            pair = position['pair']
            swap = costs[pair]['swap_long' if position['side']==1 else 'swap_short']*position['units']*days*conversion(pair,i,'Open')
            cash += swap; swap_total += swap; position['swap'] += swap
        # Existing gaps can cross stops. Exit at the worse open, never at the
        # original stop price when that price is unavailable after a gap.
        reasons,_ = risk_snapshot(i,'Open')
        if reasons:
            stopped = True; stop_reasons = reasons
            for position in list(positions):
                close(position,float(frames[position['pair']].iloc[i].Open),i,'portfolio_open_stop',at_open=True)
            ledger.append((index[i],cash))
            continue
        for position in list(positions):
            pair = position['pair']; bar = frames[pair].iloc[i]
            if position['side']==1 and bar.Open <= position['stop'] or position['side']==-1 and bar.Open >= position['stop']:
                close(position,float(bar.Open),i,'gap_stop',at_open=True)
                stopped_pairs.add(pair)
        for pair in sorted(settings):
            if pair in stopped_pairs: continue
            setting = settings[pair]; bar = frames[pair].iloc[i]
            if setting['direction']=='buy' and bar.Open <= setting['stop'] or setting['direction']=='sell' and bar.Open >= setting['stop']:
                stopped_pairs.add(pair)
                continue
            for level in levels[pair]:
                if len(positions) >= profile['max_positions']: break
                own = [p for p in positions if p['pair']==pair]
                if len(own) >= setting['max_positions']: break
                if any(abs(p['level']-level)<1e-10 for p in own): continue
                side = 1 if setting['direction']=='buy' else -1
                touched = bar.Low <= level if side==1 else bar.High >= level
                if not touched: continue
                floating, margin = mark(i,'Open')
                entry_price = (min(float(bar.Open),level) if side==1 else max(float(bar.Open),level)) + side*friction(pair)
                new_margin = entry_price*setting['quantity']*costs[pair]['margin_rate']*conversion(pair,i,'Open')
                entry_equity = cash+floating-2*friction(pair)*setting['quantity']*conversion(pair,i,'Open')
                pending = max(0.0,pending_reserve(i,'Open')-level*setting['quantity']*costs[pair]['margin_rate']*conversion(pair,i,'Open'))
                projected_positions = [dict(pair=p['pair'],side='buy' if p['side']==1 else 'sell',units=p['units']) for p in positions]
                projected_positions.append(dict(pair=pair,side=setting['direction'],units=setting['quantity']))
                projection = evaluate_portfolio(entry_equity, floating-2*friction(pair)*setting['quantity']*conversion(pair,i,'Open'),
                                                max(peak,entry_equity),margin+new_margin,pending,projected_positions,profile)
                if any(reason!='POSITION_LIMIT' for reason in projection['stop_reasons']): continue
                positions.append(dict(pair=pair,side=side,units=setting['quantity'],level=level,
                                      entry=entry_price,stop=setting['stop'],target=entry_price+side*setting['take_profit'],
                                      opened=index[i],opened_bar=i,swap=0.0))
                spread_slippage_total += friction(pair)*setting['quantity']*conversion(pair,i,'Open')
                max_count = max(max_count,len(positions))
        # For drawdown, allow the favorable extreme to precede the adverse
        # extreme. This gives an adverse path bound rather than understating
        # peak-to-trough drawdown with close-only peaks. This peak is observed
        # only after this bar's entry decisions, never during their sizing.
        favorable_reasons,_ = risk_snapshot(i,'favorable')
        # For exits, stops still precede targets. No same-bar replenishment.
        reasons, equity = risk_snapshot(i,'adverse')
        reasons = list(dict.fromkeys(favorable_reasons+reasons))
        if reasons:
            stopped = True; stop_reasons = reasons
            for position in list(positions):
                bar = frames[position['pair']].iloc[i]
                close(position,float(bar.Low if position['side']==1 else bar.High),i,'portfolio_stop')
        else:
            for position in list(positions):
                bar = frames[position['pair']].iloc[i]; side=position['side']
                stop_hit = bar.Low <= position['stop'] if side==1 else bar.High >= position['stop']
                target_hit = bar.High >= position['target'] if side==1 else bar.Low <= position['target']
                if stop_hit:
                    close(position,position['stop'],i,'stop')
                    stopped_pairs.add(position['pair'])
                elif target_hit and position['opened_bar'] < i:
                    close(position,position['target'],i,'take_profit')
        _, equity = risk_snapshot(i,'Close')
        ledger.append((index[i],equity))
    floating, margin = mark(len(index)-1)
    equity = cash+floating
    wins = sum(max(0,t['pnl']) for t in trades)
    losses = -sum(min(0,t['pnl']) for t in trades)
    streak = longest = 0
    for trade in trades:
        streak = streak+1 if trade['pnl']<0 else 0
        longest = max(longest,streak)
    months = {}
    prior = float(profile['initial_cash'])
    for timestamp,value in ledger: months[timestamp.strftime('%Y-%m')] = value
    month_returns = {}
    for month,value in months.items():
        month_returns[month] = (value/prior-1)*100 if prior>0 else None
        prior = value
    metrics = dict(net_profit=equity-profile['initial_cash'], net_return_pct=(equity/profile['initial_cash']-1)*100,
                   realized_profit=cash-profile['initial_cash'], floating_pnl=floating,
                   trades=len(trades), wins=sum(t['pnl']>0 for t in trades),
                   profit_factor=wins/losses if losses else None,
                   profit_factor_state='defined' if losses else 'no_losing_trades' if trades else 'no_trades',
                   expectancy=statistics.mean(t['pnl'] for t in trades) if trades else None,
                   max_floating_loss=max_loss,max_drawdown=max_dd,min_maintenance_pct=min_ratio,
                   max_positions=max_count,ending_positions=len(positions),max_margin=max_margin,
                   max_losing_streak=longest,swap_total=swap_total,spread_slippage_total=spread_slippage_total,
                   mean_holding_hours=statistics.mean(t['holding_hours'] for t in trades) if trades else None,
                   max_holding_hours=max((t['holding_hours'] for t in trades),default=None),
                   monthly_net_equity_return_pct=month_returns,stopped=stopped,stop_reasons=stop_reasons)
    metrics['stopped_pairs'] = sorted(stopped_pairs)
    metrics['max_pending_margin'] = max_pending_margin
    metrics['max_open_holding_hours'] = max(((index[-1]-p['opened']).total_seconds()/3600 for p in positions),default=None)
    return dict(status='RESEARCH_ONLY',economic_accepted=False,reasons=blockers,mode=mode,
                history_days=span,metrics=metrics,
                assumptions=['Fixed external grids; no indicator or future-bar signal selection',
                             'UTC-calendar-day signed swap; broker holiday/triple-swap calendar not modeled',
                             'All pairs require synchronized bars; adverse extremes are a portfolio stress bound',
                             'Drawdown assumes favorable extrema before adverse extrema; this is an OHLC path upper bound',
                             'Only deterministic finite accepted ladder levels may fill; their pending margin is reserved',
                             'Monthly boundary buckets include partial months; no annualization',
                             'No event regime or walk-forward economic acceptance has been executed'])


def chronological_split(frames, train_fraction):
    frames,index = _frames(frames)
    if not isinstance(train_fraction,(float,int)) or not 0<train_fraction<1:
        raise ValueError('Train fraction must be inside (0,1)')
    cut = int(len(index)*train_fraction)
    if cut<2 or len(index)-cut<2: raise ValueError('Both chronological partitions need at least two bars')
    return ({pair:frame.iloc[:cut].copy() for pair,frame in frames.items()},
            {pair:frame.iloc[cut:].copy() for pair,frame in frames.items()})


def select_training_candidate(training_frames, candidates, profile, costs):
    """Pure train-only selection. Caller must keep OOS frames outside this API.

    This evaluates finite fixed candidates, not a full walk-forward optimization.
    Never promotes a winning training return to economic acceptance.
    """
    return _select_training_candidate(training_frames, candidates, profile, costs, run_backtest)


def _select_training_candidate(training_frames, candidates, profile, costs, evaluate):
    if not isinstance(candidates,list) or not candidates or len(candidates)>100:
        raise ValueError('Explicit finite candidate list required (maximum 100)')
    results = [evaluate(training_frames,candidate,profile,costs) for candidate in candidates]
    eligible = [(i,result) for i,result in enumerate(results) if result['metrics'] is not None and not result['metrics']['stopped']]
    if not eligible:
        return dict(status='HOLD',candidate_index=None,economic_accepted=False,reasons=['no_eligible_training_candidate'])
    selected = max(eligible,key=lambda row: row[1]['metrics']['net_profit'])
    return dict(status='RESEARCH_ONLY',candidate_index=selected[0],economic_accepted=False,
                training_metrics=selected[1]['metrics'],candidates_evaluated=len(results),
                reasons=['training_selection_only', 'out_of_sample_and_walk_forward_required'])
