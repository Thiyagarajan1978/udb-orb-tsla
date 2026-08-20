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


def test_d1_arms_its_atr_trail_early_with_a_075_tp_scale():
    """D1 (full exit at the ATR trail) adopted adaptive_tp_scale 0.75 on 2026-08-20.

    D1's adaptive TP takes no money -- `partial_qty_pct` is 0.0, so the TP touch only ARMS the
    TRADE TASTIC chandelier. A shorter TP therefore hands more trades to the trail instead of
    leaving them to the BE stop, which carried 81% of all loss dollars at a 0% win rate. Over
    2022-01-03..2026-08-11 (ONE run, split by exit year) that is net/unit 493.6 -> 529.0, PF
    1.30 -> 1.33, maxDD -86.3 -> -66.6, positive in all five years.

    The floor stays 2.14 (lowering it added nothing) and the BE trigger stays 0.55 (0.70/0.85/1.00
    were swept and all lose). This pins the pair so a future edit cannot revert the scale while
    leaving the arming behaviour silently unchanged.
    """
    cfg = load_config(_ROOT / "config" / "tsla_config_D1.yaml")
    p = Params.from_config(cfg)
    assert p.adaptive_tp_scale == 0.75
    assert p.adaptive_tp_min == 2.14
    assert p.be_retrace_trigger == 0.55
    assert p.partial_qty_pct == 0.0                 # full exit at the trail: the TP only ARMS it
    assert cfg["enhancements"]["atr_trail_exit"]["enabled"] is True
    # The chandelier geometry stays as ported. mult 2.75/3.0 looked like a +18/unit plateau with a
    # smaller drawdown, but its whole gain is 3 days -- excluding the top 3 winners the delta is
    # NEGATIVE (-8.7), and 54 of 90 changed days are down. hhv/atr_period/hybrid_vwap all lose.
    tt = cfg["enhancements"]["atr_trail_exit"]
    assert (tt["mult"], tt["hhv_period"], tt["atr_period"], tt["hybrid_vwap"]) == (2.5, 10, 5, False)


def test_d1_cuts_new_entries_at_1130_not_noon():
    """D1 tightened its entry cutoff 12:00 -> 11:30 on 2026-08-20 (this profile only).

    The post-noon cohort adopted house-wide on 2026-07-26 understated where the damage starts: over
    2022-01-03..2026-08-11 the 11:30-12:00 entries are 28 trades at -19.4/unit and 11:00-11:30 are
    40 at -8.6, while 10:30-11:00 is still positive. Paired with adaptive_tp_scale 0.75 this is
    net 529.0 -> 548.5, PF 1.33 -> 1.36, maxDD -66.6 -> -55.0, positive in all five years, and it
    is the only cutoff that improves the 2024-25 fit window, the 2022-23 OOS years and the 2026
    holdout simultaneously.
    """
    cfg = load_config(_ROOT / "config" / "tsla_config_D1.yaml")
    tw = cfg["enhancements"]["time_window"]
    assert tw["enabled"] is True
    assert str(tw["end"]) == "11:30"
    assert str(tw["start"]) == "09:35"


def test_the_1130_cutoff_is_house_wide_on_the_tsla_profiles():
    """Extended from D1 to A1/B1/C1 + the production default on 2026-08-20.

    The 11:30-12:00 half hour is a losing cohort on the traded profiles too, over
    2022-01-03..2026-08-11: B1 23 entries at -0.52/unit average, C1 24 at -0.46, against
    11:00-11:30 at +0.54 / +0.50. Per unit B1 500.9 -> 525.1, C1 493.3 -> 515.9, A1 446.8 ->
    471.0, and it lifts the 2024-25 fit, the 2022-23 OOS years and the 2026 holdout together on
    every profile. Honest limits, recorded so a future reader does not rediscover them as a
    surprise: 2023 gets WORSE (B1 -10.1/unit), and 10:30 and 11:00 both fail the ex-top-3 breadth
    test that 11:30 passes (+10.1), so 11:30 is a spike on that column rather than a plateau.
    """
    for name in ("config.yaml", "tsla_best_A.yaml", "tsla_best_B.yaml", "tsla_config_C1.yaml",
                 "tsla_config_D1.yaml"):
        cfg = load_config(_ROOT / "config" / name)
        tw = cfg["enhancements"]["time_window"]
        assert tw["enabled"] is True, name
        assert str(tw["end"]) == "11:30", name
        assert str(tw["start"]) == "09:35", name


def test_the_cutoff_did_not_leak_into_the_non_tsla_or_parity_configs():
    """SPCX drops the three TSLA-fitted gates outright, and faithful_be035 must stay a bit-exact
    reproduction of Pine v12.4.3 -- neither may acquire an entry cutoff."""
    for name in ("spcx_orb.yaml", "spcx_c2.yaml", "faithful_be035.yaml", "tsla_config_C.yaml"):
        cfg = load_config(_ROOT / "config" / name)
        assert cfg["enhancements"]["time_window"]["enabled"] is False, name


def test_the_other_profiles_keep_the_10_tp_scale():
    """0.75 was adopted for D1 ONLY, and TESTED AND REJECTED on A1/B1/C1 on 2026-08-20.

    The mechanism does not transfer. In D1 `partial_qty_pct` is 0.0, so the adaptive TP takes no
    money -- shortening it only ARMS the chandelier sooner. On A1/B1/C1 the TP takes a real 25%,
    so shortening it caps winners as well. It still shows a positive aggregate (B1 +9.3/unit) but
    fails the breadth test that D1 passes: 383 changed days, 69 up against 314 DOWN, and ex-top-3
    the delta is -17.9. C1's equivalent knob is `atr_tp_mult` (it runs tp_mode ATR); tightening it
    to 0.225 is 417 days, 34 up / 383 down, ex-top-3 -19.2. Both make the 2026 holdout worse
    (B1 148.5 -> 140.3, C1 158.2 -> 131.5). Do not re-propose without a new mechanism.
    """
    for name in ("tsla_best_A.yaml", "tsla_best_B.yaml", "tsla_config_C1.yaml"):
        p = Params.from_config(load_config(_ROOT / "config" / name))
        assert p.adaptive_tp_scale == 1.0, name


def test_all_runner_is_documented_but_not_adopted():
    """Pine v3.9.15 added an "All-runner exit" tick-box (default OFF). Its Python twin is
    `partial_qty_pct: 0.0` with `use_partial_exit: true` -- exactly what D1 already ships.

    Measured 2026-08-20 over 2022-01-03..2026-08-19 @60 shares: A1 $28,450 -> $31,664 (+11.3%),
    B1 $31,507 -> $35,772 (+13.5%), C1 $31,389 -> $35,754 (+13.9%), D1 bit-identical. Better in
    the OOS years, the fit window and the 2026 holdout at once, and net/DD improves on all three
    -- but the win rate FALLS (B1 46.0 -> 44.3%) and B1's day-level delta is 160 up / 185 DOWN,
    so it trades more red days for bigger winners. NOT adopted: 25% is what is live and
    reconciled. This test exists so a Pine-side default flip cannot silently drift the twins.
    """
    for name in ("tsla_best_A.yaml", "tsla_best_B.yaml", "tsla_config_C1.yaml"):
        p = Params.from_config(load_config(_ROOT / "config" / name))
        assert p.use_partial_exit is True, name
        assert p.partial_qty_pct == 25.0, name
    d1 = Params.from_config(load_config(_ROOT / "config" / "tsla_config_D1.yaml"))
    assert d1.use_partial_exit is True                 # the TP still ARMS the trail
    assert d1.partial_qty_pct == 0.0                   # ...but takes nothing: already all-runner
