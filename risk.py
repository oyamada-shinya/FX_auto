"""Pure research risk checks. Callers must keep portfolio inputs/outputs private.

No I/O, credentials, brokerage connection, or execution is provided here.
Broker rule source: https://www.matsui.co.jp/fx/rule/ (checked 2026-11-18).
Margin amounts must already be converted to the account currency by the caller.
"""
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, localcontext
import math


PAIR_RULES = {
    'USD/JPY': ('0.001', '0.01', '10'),
    'MXN/JPY': ('0.001', '0.01', '3'),
    'AUD/USD': ('0.00001', '0.0001', '10'),
    'EUR/USD': ('0.00001', '0.0001', '10'),
    'AUD/JPY': ('0.001', '0.01', '10'),
}
ZERO = Decimal('0')


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f'{name} must be a finite number')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{name} must be a finite number') from None
    if not result.is_finite():
        raise ValueError(f'{name} must be a finite number')
    _json_float(result, name)
    return result


def _json_float(value, name):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f'{name} is outside the supported numeric range')
    if result == 0 and value != 0:
        raise ValueError(f'{name} is outside the supported numeric range')
    return result


def _positive(value, name):
    result = _number(value, name)
    if result <= 0:
        raise ValueError(f'{name} must be positive')
    return result


def _positive_int(value, name, maximum=None):
    result = _positive(value, name)
    if result != result.to_integral_value() or (maximum is not None and result > maximum):
        raise ValueError(f'{name} must be a positive integer within its limit')
    return int(result)


def evaluate_portfolio(equity, floating_pnl, peak_equity, position_margin,
                       pending_margin, positions, limits):
    """Evaluate a private, synchronized account-currency snapshot.

    ``positions`` is a list/tuple of {pair, side, units} records. Opposite sides
    count separately. ``limits`` must contain all four documented keys. This
    checks current state only: a caller must re-evaluate each projected fill
    and adverse scenario before accepting any hypothetical new order.
    """
    if not isinstance(limits, Mapping):
        raise ValueError('limits must be a mapping')
    required = ('max_floating_loss', 'max_drawdown', 'min_maintenance_pct', 'max_positions')
    if not set(required).issubset(limits):
        raise ValueError('All risk limits are required')
    cap_loss = _positive(limits['max_floating_loss'], 'max_floating_loss')
    cap_drawdown = _positive(limits['max_drawdown'], 'max_drawdown')
    min_maintenance = _positive(limits['min_maintenance_pct'], 'min_maintenance_pct')
    cap_positions = _positive_int(limits['max_positions'], 'max_positions')
    equity = _number(equity, 'equity')
    floating_pnl = _number(floating_pnl, 'floating_pnl')
    peak_equity = _positive(peak_equity, 'peak_equity')
    position_margin = _number(position_margin, 'position_margin')
    pending_margin = _number(pending_margin, 'pending_margin')
    if position_margin < 0 or pending_margin < 0:
        raise ValueError('Margin amounts must be non-negative')
    if peak_equity < equity:
        raise ValueError('peak_equity cannot be below current equity')
    if not isinstance(positions, (list, tuple)):
        raise ValueError('positions must be a list or tuple of gross position records')
    for position in positions:
        if (not isinstance(position, Mapping) or not isinstance(position.get('pair'), str)
                or position.get('pair') not in PAIR_RULES
                or position.get('side') not in ('buy', 'sell')):
            raise ValueError('Invalid position record')
        _positive_int(position.get('units'), 'position units')
    if not positions and position_margin != 0:
        raise ValueError('Position margin requires position records')

    with localcontext() as context:
        context.prec = 50
        floating_loss = max(ZERO, -floating_pnl)
        drawdown = peak_equity - equity
        free_margin = equity - position_margin - pending_margin
        maintenance = ((equity - pending_margin) / position_margin * 100
                       if position_margin > 0 else None)
        reasons = []
        if equity <= 0:
            reasons.append('NON_POSITIVE_EQUITY')
        if floating_loss >= cap_loss:
            reasons.append('FLOATING_LOSS_LIMIT')
        if drawdown >= cap_drawdown:
            reasons.append('DRAWDOWN_LIMIT')
        if maintenance is not None and maintenance <= min_maintenance:
            reasons.append('MAINTENANCE_LIMIT')
        if maintenance is None and positions:
            reasons.append('POSITION_MARGIN_UNAVAILABLE')
        if free_margin <= 0:
            reasons.append('NO_FREE_MARGIN')
        if len(positions) >= cap_positions:
            reasons.append('POSITION_LIMIT')
        return {
            'evaluation_scope': 'CURRENT_SNAPSHOT_ONLY',
            'new_orders_allowed': not reasons,
            'stop_reasons': reasons,
            'gross_positions': len(positions),
            'floating_loss': _json_float(floating_loss, 'floating_loss'),
            'drawdown': _json_float(drawdown, 'drawdown'),
            'free_margin': _json_float(free_margin, 'free_margin'),
            'maintenance_pct': (_json_float(maintenance, 'maintenance_pct')
                                if maintenance is not None else None),
            'maintenance_status': ('CALCULATED' if maintenance is not None
                                   else 'NO_POSITION_MARGIN'),
        }


def validate_setting(pair, side, lower, upper, spacing_pips, profit_pips,
                     units, stop, max_positions):
    """Validate a finite research candidate, never authorize brokerage orders.

    ``max_positions`` is a research safety cap, not a native Matsui setting.
    The inclusive ladder count is conservative; it is not a claim to reproduce
    the broker's order-generation algorithm. Existing positions, live quotes,
    exact order count and portfolio acceptance require separate verification.
    """
    if not isinstance(pair, str) or pair not in PAIR_RULES or side not in ('buy', 'sell'):
        raise ValueError('Unsupported pair or side')
    tick, pip, minimum_pips = map(Decimal, PAIR_RULES[pair])
    lower = _positive(lower, 'lower')
    upper = _positive(upper, 'upper')
    spacing_pips = _positive(spacing_pips, 'spacing_pips')
    profit_pips = _positive(profit_pips, 'profit_pips')
    units = _positive_int(units, 'units', maximum=2000000)
    stop = _positive(stop, 'stop')
    cap = _positive_int(max_positions, 'max_positions', maximum=100)
    with localcontext() as context:
        context.prec = 50
        if lower >= upper:
            raise ValueError('lower must be below upper')
        if any(value % tick != 0 for value in (lower, upper, stop)):
            raise ValueError('Prices must align to the pair tick size')
        if spacing_pips < minimum_pips or profit_pips < minimum_pips:
            raise ValueError('Spacing and take profit must satisfy broker minimums')
        if spacing_pips % Decimal('0.1') != 0 or profit_pips % Decimal('0.1') != 0:
            raise ValueError('Pip widths support at most one decimal place')
        if (side == 'buy' and stop >= lower) or (side == 'sell' and stop <= upper):
            raise ValueError('Stop must be beyond the adverse edge of the range')
        spacing_price = spacing_pips * pip
        profit_price = profit_pips * pip
        levels = int((upper - lower) // spacing_price) + 1
        if levels > cap or levels > 100:
            raise ValueError('Conservative ladder exceeds finite position/group cap')
        return {
            'valid': True,
            'mode': 'research_only',
            'decision': 'HOLD',
            'new_orders_allowed': False,
            'stop_reasons': ['LEGACY_POSITIONS_UNVERIFIED', 'PORTFOLIO_ACCEPTANCE_UNVERIFIED',
                             'BROKER_ORDER_GENERATION_UNVERIFIED'],
            'pair': pair,
            'side': side,
            'tick_size': _json_float(tick, 'tick_size'),
            'pip_size': _json_float(pip, 'pip_size'),
            'spacing_price': _json_float(spacing_price, 'spacing_price'),
            'profit_price': _json_float(profit_price, 'profit_price'),
            'units': units,
            'max_positions': cap,
            'conservative_ladder_levels': levels,
            'range_or_spacing_change_requires_replacement': True,
        }
