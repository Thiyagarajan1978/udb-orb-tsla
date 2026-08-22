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
    # partial_qty_pct ALSO differs since 2026-08-20: the default adopted all-runner (0.0)
    # while the Pine v12.4.3 port keeps its 25% scale-out — asserted per-config below.
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
    assert p.partial_qty_pct == 0.0                 # ALL-RUNNER adopted 2026-08-20 (Pine v3.9.16)


def test_faithful_port_config_preserves_035():
    """The faithful-port config reproduces the exact Pine v12.4.3 value."""
    cfg = load_config(_ROOT / "config" / "faithful_be035.yaml")
    p = Params.from_config(cfg)
    _assert_common_profile(p)
    assert p.be_retrace_trigger == 0.35             # exact Pine port
    assert p.adaptive_tp_scale == 1.0               # exact Pine port
    assert p.partial_qty_pct == 25.0                # exact Pine port: 25% off at the TP, 75%
                                                    # trails. The DEFAULT moved to all-runner
                                                    # on 2026-08-20; this config must not.


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


def test_all_runner_is_the_adopted_default():
    """ADOPTED 2026-08-20 (Pine v3.9.16 "All-runner exit", ticked by default): `partial_qty_pct`
    0.0 with `use_partial_exit` true, so the adaptive TP takes NOTHING -- its touch only ARMS the
    runner -- and 100% leaves on one exit. A1/B1/C1 joined D1, which has shipped this since
    2026-07-23.

    Measured over 2022-01-03..2026-08-19 @60 shares: A1 $28,450 -> $31,664 (+11.3%), B1 $31,507 ->
    $35,772 (+13.5%), C1 $31,389 -> $35,754 (+13.9%), D1 bit-identical (which is the check that
    the Pine generalisation is faithful). Better in the 2022-23 OOS years, the 2024-25 fit window
    AND the 2026 holdout at once; net/DD up on all three; trade count IDENTICAL. Cost: the win
    rate FALLS (B1 46.0 -> 44.3%) and B1 runs 160 up days against 185 DOWN for +71.1/unit
    (ex-top-3 +56.7) -- bigger winners, more red days.

    NOT the same as `use_partial_exit: false` (exit 100% AT the target), which is 34-42% WORSE on
    every profile: it caps the winners while the BE-stop cohort, 87% of all loss dollars, is
    untouched. This test pins the pairing so a config edit cannot silently reintroduce either the
    old scale-out or the full-TP exit.
    """
    for name in ("tsla_best_A.yaml", "tsla_best_B.yaml", "tsla_config_C1.yaml",
                 "tsla_config_D1.yaml", "config.yaml"):
        p = Params.from_config(load_config(_ROOT / "config" / name))
        assert p.use_partial_exit is True, name      # the TP must still ARM the runner
        assert p.partial_qty_pct == 0.0, name        # ...but take nothing


def test_all_runner_did_not_leak_into_the_parity_or_spcx_configs():
    """faithful_be035 must stay a bit-exact Pine v12.4.3 reproduction, and SPCX was validated with
    the 25% partial ON -- all-runner was never tested on that symbol, and `runner_trail` is inert
    there without a partial. Neither may inherit the TSLA adoption."""
    for name in ("faithful_be035.yaml", "spcx_orb.yaml"):
        p = Params.from_config(load_config(_ROOT / "config" / name))
        assert p.partial_qty_pct == 25.0, name


def test_pd_level_exit_is_ahead_only():
    """RE-ADOPTED 2026-08-20 (Pine v3.9.17), reversing the 2026-08-11 "both sides" call.

    Only a level the trade is moving TOWARD may trigger the tag-and-reject exit. The 2026-08-11
    call to also watch levels BEHIND the entry was taken against the measurement of the day, and
    the rig it was judged on no longer exists -- the 11:30 cutoff (v3.9.14) and all-runner
    (v3.9.16) both landed afterwards.

    Re-measured on the current rig, 2022-01-03..2026-08-19 @60 shares after $0.10/share:
    A1 $31,664 -> $33,445 (+5.6%), B1 $35,772 -> $37,699 (+5.4%), C1 $35,754 -> $37,242 (+4.2%),
    D1 $33,082 -> $33,417 (+1.0%). Max drawdown falls on all four (B1 $3,962 -> $3,393) and
    net/DD rises on all four (B1 9.03 -> 11.11).

    LIMITS, on the record: it wins 2022 and 2023 on every profile but LOSES 2026 on every profile
    (-6.7 to -10.3/unit), and 2026 is the only year the "both sides" motivating days came from;
    C1 also loses 2025 and D1 loses 2024 and 2025. Breadth is more DOWN days than up on all four
    (B1 33 up / 42 down) and ex-top-3 is thin to negative (C1 +$7, D1 -$1,006, so D1 fails that
    test). What carries it is the pre-2025-08-11 slice, out of sample for this knob, which is up
    on all four (B1 317.9 -> 356.9/unit).

    SPCX is deliberately excluded: its pd_level_exit is disabled outright and the knob was
    measured and TV-validated on TSLA only.
    """
    for name in ("tsla_best_A.yaml", "tsla_best_B.yaml", "tsla_config_C1.yaml",
                 "tsla_config_D1.yaml", "config.yaml"):
        cfg = load_config(_ROOT / "config" / name)
        pdx = cfg["enhancements"]["pd_level_exit"]
        assert pdx["enabled"] is True, name
        assert pdx["ahead_only"] is True, name


def test_confirmation_candle_stays_off():
    """The confirmation candle must not creep back on.

    It was disabled 2026-07-11 with the note "re-enable only if you revert to close-fill
    (stop_fill_mode: close)" -- and B1/C1 adopted close-fill three days later, so that instruction
    became an argument FOR ticking it. Measured under close-fill on the v3.9.17 rig,
    2022-01-03..2026-08-19 @60 shares after $0.10/share, ON vs OFF: A1 $30,463 vs $33,445 (-8.9%),
    B1 $34,784 vs $37,699 (-7.7%), C1 $32,441 vs $37,242 (-12.9%), D1 $25,205 vs $33,417 (-24.6%).

    It is a genuine filter -- ~170 fewer trades and the win rate RISES (B1 46.4 -> 48.4%) -- which
    is exactly why it deceives: it removes winners with the losers. Per year it wins 2025 on all
    four profiles and loses 2023 AND 2026 on all four, 1-2 of 5 years. Breadth is ~270 up against
    ~660 down over the 930 days it changes, and ex-top-3 is negative on all four.
    """
    for name in ("tsla_best_A.yaml", "tsla_best_B.yaml", "tsla_config_C1.yaml",
                 "tsla_config_D1.yaml", "config.yaml"):
        cfg = load_config(_ROOT / "config" / name)
        assert cfg["enhancements"]["confirm_breakout"]["enabled"] is False, name


def test_pine_v3_defaults_track_the_configs():
    """The Pine headers are the one place a default can drift with nothing to catch it.

    A TradingView chart keeps its SAVED inputs across a paste, so a stale setting is invisible
    unless something compares the two lists -- which is how the exported v3.9.16 charts turned out
    to be running `confirmBreakout` ON and `pdxAhead` ON against configs that said otherwise. This
    pins the three defaults adopted in v3.9.17 to the Pine sources themselves.
    """
    ind = (_ROOT / "pine" / "UDB_ORB_TSLA_v3.pine").read_text(encoding="utf-8")
    strat = (_ROOT / "pine" / "UDB_ORB_TSLA_v3_strategy.pine").read_text(encoding="utf-8")

    for src, label in ((ind, "indicator"), (strat, "strategy")):
        assert 'pdxAhead   = input.bool(true,' in src, label
        assert 'confirmBreakout    = input.bool(false,' in src, label

    # The Strategy Tester models no slippage unless the header sets it; 10 ticks = $0.10/share on
    # TSLA, matching what the Python engine charges. At 0 every tester run reads ~13% high.
    assert "slippage                = 10," in strat
