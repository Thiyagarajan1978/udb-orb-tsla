"""Profile resolution: the production default (tuned BE 0.55) and the exact Pine port."""
from pathlib import Path

from conftest import base_config

from udb_orb.config import load_config
from udb_orb.engine.params import Params

_ROOT = Path(__file__).resolve().parents[1]


def _assert_common_profile(p: Params):
    """Everything that is identical between the default and the faithful port."""
    assert p.use_adaptive_tp is True
    assert p.adaptive_tp_min == 2.14
    # adaptive_tp_scale differs (default tuned 1.25 vs port 1.0) — asserted per-config below
    assert p.partial_qty_pct == 25.0
    assert p.use_partial_exit is True
    assert p.use_be_retrace is True
    assert p.be_retrace_use_close is False          # NOT Pure Trail -> wick based
    assert p.be_trail_amount == 0.25
    assert p.partial_activation == 1.00
    assert p.use_reversal is True
    assert p.reversal_qty_mult == 2.0
    assert p.reversal_target == 5.0
    assert p.apply_be_to_reversal is True
    assert p.buffer_pct_or == 10.0
    assert p.trade_side_mode == "Both"
    assert p.allow_longs and p.allow_shorts


def test_default_profile_uses_tuned_be_055():
    """The shipped default adopts the tuned BE trigger (validated train+holdout)."""
    p = Params.from_config(base_config())
    _assert_common_profile(p)
    assert p.be_retrace_trigger == 0.55             # adopted tuned default
    assert p.adaptive_tp_scale == 1.0               # re-tuned under realistic exit_on_close


def test_faithful_port_config_preserves_035():
    """The faithful-port config reproduces the exact Pine v12.4.3 value."""
    cfg = load_config(_ROOT / "config" / "faithful_be035.yaml")
    p = Params.from_config(cfg)
    _assert_common_profile(p)
    assert p.be_retrace_trigger == 0.35             # exact Pine port
    assert p.adaptive_tp_scale == 1.0               # exact Pine port


def test_traded_configs_adopt_close_triggered_stop():
    """B1 + C1 (the TRADED profiles) default to the CLOSE-triggered stop (adopted 2026-07-14):
    the stop fires only when a bar CLOSES beyond the level. Walk-forward over 2022-2026 this beat
    the wick/resting stop by +42-46% net with ~40% smaller drawdown and flipped 2024 from a loss to
    a profit (OOS-confirmed on 2022-23), and the wired Pine strategy reconciles to it within 1-3%.
    This asserts the default cannot silently revert to the wick/touch stop."""
    for name in ("tsla_best_B.yaml", "tsla_config_C1.yaml"):
        p = Params.from_config(load_config(_ROOT / "config" / name))
        assert p.exit_on_close is True, f"{name}: close-trigger not resolved"
        assert p.stop_fill_touch is False, f"{name}: must not use the wick/touch resting fill"
        assert p.be_lag is False, f"{name}: be_lag is a wick-mode concern, off under close"
