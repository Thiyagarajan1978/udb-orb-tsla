"""The SPCX configs: the three TSLA-fitted gates must stay OFF, and the $8 OR cap must stay ON.

SPCX was adopted 2026-08-12 on the finding that dropping the gates fitted on TSLA -- the 4.92% vol
cap, the 12:00 entry cutoff and the PD-level tag-and-reject exit -- takes B1 from +22.4% to +49.3%
AND improves every robustness column. The obvious future accident is someone syncing an SPCX yaml
against a TSLA one and quietly restoring a gate; that would look like a tidy-up in review and would
silently cost about half the measured edge, so it is asserted rather than trusted to a comment.

The `max_or_width $8` cap is the deliberate exception and is asserted ON: it was tested on SPCX and
removing it was WORSE (return down, worst trade doubled). "Drop the TSLA gates" is not "drop
everything", and that distinction is the whole reason this file exists.
"""
from pathlib import Path

from udb_orb.config import load_config
from udb_orb.engine.params import Params

_ROOT = Path(__file__).resolve().parents[1]

# label -> (yaml, adaptive_tp_min, use_partial_exit)
_SPCX = {
    "B1": ("config/spcx_orb.yaml", 2.14, True),    # adopted
    "C2": ("config/spcx_c2.yaml", 0.0, False),     # research comparison, OR-width TP, no partial
}


def _cfg(name):
    return load_config(_ROOT / _SPCX[name][0])


def test_spcx_configs_target_spcx():
    for label in _SPCX:
        cfg = _cfg(label)
        assert cfg["symbol"] == "SPCX"
        # the label scopes the DB run record and the live alert seen-set; the two files share a
        # db_path and a profile NAME, so identical labels would make their events indistinguishable
        assert cfg["profile"]["label"] == label


def test_spcx_tsla_fitted_gates_are_off():
    """vol cap / noon cutoff / PD-level exit: all three OFF on every SPCX config."""
    for label in _SPCX:
        enh = _cfg(label)["enhancements"]
        assert enh["volatility_regime"]["enabled"] is False, label
        assert enh["time_window"]["enabled"] is False, label
        # stated explicitly in both files rather than relying on the engine's absent-means-off
        assert enh["pd_level_exit"]["enabled"] is False, label


def test_spcx_keeps_the_or_width_cap():
    """Kept ON deliberately -- removing it was measured on SPCX and was worse."""
    for label in _SPCX:
        p = Params.from_config(_cfg(label))
        assert p.max_or_width_enabled is True, label
        assert p.max_or_width == 8.0, label


def test_spcx_dollar_params_are_not_rescaled():
    """Range- and price-scaling the dollar knobs were both tested on SPCX and both were worse.

    SPCX (~$146) sits in a TSLA-like band, so the knobs are used as-is. If someone ever adds an
    auto-scaler, this catches it doing so silently.
    """
    for label in _SPCX:
        p = Params.from_config(_cfg(label))
        assert p.be_trail_amount == 0.25, label
        assert p.reversal_target == 5.0, label
        assert p.reversal_risk_cap == 6.0, label
        assert p.fixed_sl == 5.0, label


def test_spcx_profiles_differ_only_as_documented():
    """B1 vs C2 is the TP floor and the partial -- nothing else drifted between the two files."""
    for label, (_, tp_min, partial) in _SPCX.items():
        p = Params.from_config(_cfg(label))
        assert p.use_adaptive_tp is True, label
        assert p.adaptive_tp_scale == 1.0, label
        assert p.adaptive_tp_min == tp_min, label
        assert p.use_partial_exit is partial, label
        # shared by both: the risk controls are NOT what the SPCX treatment changes
        assert p.be_retrace_trigger == 0.55, label
        assert p.reversal_risk_mode == "scale", label
        assert p.buffer_pct_or == 10.0, label
        assert p.slippage_per_unit == 0.1, label


def test_spcx_changes_did_not_leak_into_tsla():
    """The TSLA configs must be untouched by any of this."""
    for y in ("config/config.yaml", "config/tsla_best_B.yaml", "config/tsla_config_C.yaml"):
        cfg = load_config(_ROOT / y)
        assert cfg["symbol"] == "TSLA", y
        assert cfg["enhancements"]["volatility_regime"]["enabled"] is True, y
