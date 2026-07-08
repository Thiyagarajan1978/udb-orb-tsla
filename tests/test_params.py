"""The resolved profile must match the Pine v12.4.3 Adaptive TP + Reversal @ 5m."""
from conftest import base_config

from udb_orb.engine.params import Params


def test_adaptive_reversal_profile_values():
    p = Params.from_config(base_config())
    assert p.use_adaptive_tp is True
    assert p.adaptive_tp_scale == 1.0
    assert p.adaptive_tp_min == 2.14
    assert p.partial_qty_pct == 25.0
    assert p.use_partial_exit is True
    assert p.use_be_retrace is True
    assert p.be_retrace_use_close is False          # NOT Pure Trail -> wick based
    assert p.be_retrace_trigger == 0.35             # 5m auto-tune
    assert p.be_trail_amount == 0.25
    assert p.partial_activation == 1.00
    assert p.use_reversal is True
    assert p.reversal_qty_mult == 2.0
    assert p.reversal_target == 5.0
    assert p.apply_be_to_reversal is True
    assert p.buffer_pct_or == 10.0
    assert p.trade_side_mode == "Both"
    assert p.allow_longs and p.allow_shorts
