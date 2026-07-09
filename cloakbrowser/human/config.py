"""cloakbrowser-human — Configuration and presets.

All numeric parameters for human-like behavior are centralized here.
Two built-in presets: 'default' (normal human speed) and 'careful' (slower, more cautious).
"""

from __future__ import annotations

import logging
import math
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal, Tuple, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

Range = Tuple[float, float]
HumanPreset = Literal["default", "careful"]

# ---------------------------------------------------------------------------
# Module-level constants (non-scroll magic numbers)
# ---------------------------------------------------------------------------
# Cryptographically-secure RNG (secrets.SystemRandom) — used by all rand*().
# MT19937 (the `random` module) is predictable and was flagged as a fingerprint
# / exploit surface. SystemRandom pulls from the OS CSPRNG.
_rng = secrets.SystemRandom()

# Mouse movement
CONTROL_POINT_BIAS = 0.3  # ± fraction of distance for Bezier control points
WOBBLE_HALF_RANGE = 0.5   # (rng() - WOBBLE_HALF_RANGE) * 2 * amp
MOUSE_OVERSHOOT_REBOUND_PX = 4   # jitter magnitude when correcting overshoot
MOUSE_OVERSHOOT_REBOUND_DELAY_MS = (30, 70)  # delay after overshoot correction
MOUSE_BURST_LOGNORMAL_SIGMA = 0.25

# Click target fractions (inside the bounding box)
CLICK_INPUT_X_RANGE = (0.05, 0.30)  # default for inputs (overridable via config)
CLICK_INPUT_Y_RANGE = (0.30, 0.70)
CLICK_BUTTON_X_RANGE = (0.35, 0.65)
CLICK_BUTTON_Y_RANGE = (0.35, 0.65)

# Idle drift
IDLE_DRIFT_HALF = 0.5  # (rng() - IDLE_DRIFT_HALF) * 2 * idle_drift_px

# Keyboard typing
INTER_CHAR_DELAY_MIN_MS = 10
TYPING_DELAY_SPREAD_HALF = 0.5  # (rng() - half) * 2 * spread


class HumanConfigOverrides(TypedDict, total=False):
    typing_delay: float
    typing_delay_spread: float
    typing_pause_chance: float
    typing_pause_range: Range
    shift_down_delay: Range
    shift_up_delay: Range
    key_hold: Range
    field_switch_delay: Range
    mistype_chance: float
    mistype_delay_notice: Range
    mistype_delay_correct: Range
    mouse_steps_divisor: float
    mouse_min_steps: int
    mouse_max_steps: int
    mouse_wobble_max: float
    mouse_overshoot_chance: float
    mouse_overshoot_px: Range
    mouse_burst_size: Range
    mouse_burst_pause: Range
    click_aim_delay_input: Range
    click_aim_delay_button: Range
    click_hold_input: Range
    click_hold_button: Range
    click_input_x_range: Range
    idle_drift_px: float
    idle_pause_range: Range
    scroll_delta_base: Range
    scroll_delta_variance: float
    scroll_pause_fast: Range
    scroll_pause_slow: Range
    scroll_accel_steps: Range
    scroll_decel_steps: Range
    scroll_overshoot_chance: float
    scroll_overshoot_px: Range
    scroll_settle_delay: Range
    scroll_target_zone: Range
    scroll_pre_move_delay: Range
    read_pause_chance: float
    read_pause_range: Range
    read_backscroll_chance: float
    read_backscroll_px: Range
    scroll_lognormal_timing: bool
    mouse_lognormal_pause: bool
    initial_cursor_x: Range
    initial_cursor_y: Range
    idle_between_actions: bool
    idle_between_duration: Range


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class HumanConfig:
    """All tunable parameters for human-like behavior."""

    # Keyboard
    typing_delay: float = 70
    typing_delay_spread: float = 40
    typing_pause_chance: float = 0.1
    typing_pause_range: Range = (400, 1000)
    shift_down_delay: Range = (30, 70)
    shift_up_delay: Range = (20, 50)
    key_hold: Range = (15, 35)
    
    # Mistype (typo simulation)
    mistype_chance: float = 0.02
    mistype_delay_notice: Range = (100, 300)
    mistype_delay_correct: Range = (50, 150)

    field_switch_delay: Range = (800, 1500)

    # Mouse — movement
    mouse_steps_divisor: float = 8
    mouse_min_steps: int = 25
    mouse_max_steps: int = 80
    mouse_wobble_max: float = 1.5
    mouse_overshoot_chance: float = 0.15
    mouse_overshoot_px: Range = (3, 6)
    mouse_burst_size: Range = (3, 5)
    mouse_burst_pause: Range = (8, 18)

    # Mouse — use lognormal timing (P1-1)
    mouse_lognormal_pause: bool = True

    # Mouse — clicks
    click_aim_delay_input: Range = (60, 140)
    click_aim_delay_button: Range = (80, 200)
    click_hold_input: Range = (40, 100)
    click_hold_button: Range = (60, 150)
    click_input_x_range: Range = (0.05, 0.30)

    # Mouse — idle
    idle_drift_px: float = 3
    idle_pause_range: Range = (300, 1000)

    # Scroll
    scroll_delta_base: Range = (80, 130)
    scroll_delta_variance: float = 0.2
    scroll_pause_fast: Range = (30, 80)
    scroll_pause_slow: Range = (80, 200)
    scroll_accel_steps: Range = (2, 3)
    scroll_decel_steps: Range = (2, 3)
    scroll_overshoot_chance: float = 0.1
    scroll_overshoot_px: Range = (50, 150)
    scroll_settle_delay: Range = (300, 600)
    scroll_target_zone: Range = (0.20, 0.80)
    scroll_pre_move_delay: Range = (100, 300)

    # Scroll — reading behaviour (P2-1)
    # Real users pause to read content mid-scroll, and occasionally
    # back-scroll to re-read something they missed or found interesting.
    read_pause_chance: float = 0.30
    read_pause_range: Range = (3000, 8000)
    read_backscroll_chance: float = 0.15
    read_backscroll_px: Range = (100, 300)

    # Scroll — use lognormal timing (P1-1)
    # When True, scroll pauses and delta sizes use rand_lognormal() instead
    # of rand_range(), producing a more human-like distribution.
    scroll_lognormal_timing: bool = True

    # Initial cursor position (as if coming from the address bar area)
    initial_cursor_x: Range = (400, 700)
    initial_cursor_y: Range = (45, 60)

    # Idle micro-movements between actions (opt-in, adds latency)
    idle_between_actions: bool = False
    idle_between_duration: Range = (0.3, 0.8)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

def _careful_config() -> HumanConfig:
    """Careful preset — everything slower and more deliberate."""
    return HumanConfig(
        # Keyboard — slower typing
        typing_delay=100,
        typing_delay_spread=50,
        typing_pause_chance=0.15,
        typing_pause_range=(500, 1200),
        shift_down_delay=(40, 90),
        shift_up_delay=(30, 70),
        key_hold=(20, 45),
        field_switch_delay=(1000, 2000),
        # Mouse — slower, more precise
        mouse_overshoot_chance=0.10,
        mouse_burst_pause=(12, 25),
        # Mouse — clicks (longer aiming and holding)
        click_aim_delay_input=(80, 180),
        click_aim_delay_button=(120, 280),
        click_hold_input=(60, 140),
        click_hold_button=(80, 200),
        # Scroll — slower
        scroll_pause_fast=(100, 200),
        scroll_pause_slow=(250, 600),
        scroll_settle_delay=(400, 800),
        scroll_pre_move_delay=(150, 400),
        # Reading — more pausing (careful reader)
        read_pause_chance=0.40,
        read_pause_range=(5000, 12000),
        read_backscroll_chance=0.20,
        # Idle between actions enabled for careful preset
        idle_between_actions=True,
        idle_between_duration=(0.4, 1.0),
    )


_PRESETS: dict[str, HumanConfig] = {
    "default": HumanConfig(),
    "careful": _careful_config(),
}


def register_preset(name: str, cfg: HumanConfig) -> None:
    """Register a custom humanize preset under ``name``.

    Allows applications to define their own named presets (e.g. a 'fast'
    preset) and reference them via ``human_preset=name`` in ``launch()``.
    Overwriting an existing name is allowed.
    """
    if not name or not isinstance(name, str):
        raise ValueError("register_preset requires a non-empty string name")
    if not isinstance(cfg, HumanConfig):
        raise TypeError("register_preset requires a HumanConfig instance")
    _PRESETS[name] = cfg


def set_seed(seed: int | float | str | bytes | bytearray | None) -> None:
    """Seed the RNG — **testing only**.

    ``secrets.SystemRandom`` ignores seeding (it always reads from the OS
    CSPRNG), so this swaps in a deterministic ``random.Random(seed)`` for
    reproducible test runs. Using it in production re-introduces the
    predictable-RNG weakness this module was fixed to avoid.

    A warning is logged every time this is called so accidental production
    use is visible.
    """
    import random as _random
    global _rng
    logger.warning(
        "set_seed() called — switching human RNG to a DETERMINISTIC "
        "random.Random(%r). This MUST NOT be used in production.", seed
    )
    _rng = _random.Random(seed)  # type: ignore[assignment]


def resolve_config(
    preset: HumanPreset = "default",
    overrides: HumanConfigOverrides | None = None,
) -> HumanConfig:
    """Resolve a preset name + optional overrides into a full HumanConfig.

    Args:
        preset: 'default' or 'careful'.
        overrides: Typed mapping of HumanConfig field names to override values.

    Returns:
        A new HumanConfig instance.

    Raises:
        ValueError: If preset is not a recognized name.
    """
    if preset not in _PRESETS:
        raise ValueError(
            f"Unknown humanize preset {preset!r}. "
            f"Valid presets: {', '.join(sorted(_PRESETS.keys()))}"
        )
    base = _PRESETS[preset]
    if not overrides:
        return HumanConfig(**{k: getattr(base, k) for k in base.__dataclass_fields__})
    merged = {k: getattr(base, k) for k in base.__dataclass_fields__}
    merged.update(overrides)
    return HumanConfig(**merged)


def merge_config(base: HumanConfig, overrides: dict | None) -> HumanConfig:
    """Merge ``overrides`` (a dict of HumanConfig field names → values) on top of
    ``base``. Returns a new HumanConfig — ``base`` is never mutated.

    Used by per-call overrides like ``page.type(sel, text, human_config={...})``
    so the same page can use different timings for different inputs without
    re-patching.

    Unknown keys are logged at WARNING level (rather than silently ignored) to
    help callers spot typos in override names, but are still skipped so the
    call remains forgiving.
    """
    if not overrides:
        return base
    merged = {k: getattr(base, k) for k in base.__dataclass_fields__}
    for k, v in overrides.items():
        if k in base.__dataclass_fields__:
            merged[k] = v
        else:
            logger.warning(
                "merge_config: ignoring unknown HumanConfig override %r "
                "(not a known field). Typo?", k
            )
    return HumanConfig(**merged)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def rand_unit() -> float:
    """Random float in [0.0, 1.0) from the CSPRNG."""
    return _rng.random()


def rand(lo: float, hi: float) -> float:
    """Random float in [lo, hi]."""
    return _rng.uniform(lo, hi)


def rand_int(lo: int, hi: int) -> int:
    """Random integer in [lo, hi] inclusive."""
    return _rng.randint(lo, hi)


def rand_range(r: Range) -> float:
    """Random float from a (min, max) tuple."""
    return _rng.uniform(r[0], r[1])


def rand_int_range(r: Range) -> int:
    """Random integer from a (min, max) tuple, inclusive."""
    return _rng.randint(int(r[0]), int(r[1]))


def rand_lognormal(median: float, spread: float = 0.3) -> float:
    """Random value from a log-normal distribution centred on ``median``.

    Real human motor movements follow log-normal timing: most actions
    cluster near a central value, but occasional outliers stretch the tail
    (e.g. a pause that takes 4× longer than usual).  ``spread`` controls
    how wide the tail is — 0.2 is narrow (most values close to median),
    0.5 is wide (long, irregular tails).

    ``rand_unit`` (the default for the rest of the config) produces a
    flat distribution that is statistically distinguishable from human
    behaviour.  This function is used for mouse burst pauses, scroll-step
    intervals, and other timing-sensitive parameters where log-normality
    matters most.
    """
    if median <= 0:
        raise ValueError(
            f"rand_lognormal requires median > 0 (got {median!r}); "
            f"log-normal is undefined for non-positive medians."
        )
    mu = math.log(median)
    sigma = max(spread, 0.01)
    return _rng.lognormvariate(mu, sigma)


def sleep_ms(ms: float) -> None:
    """Sleep for `ms` milliseconds."""
    if ms > 0:
        time.sleep(ms / 1000.0)


async def async_sleep_ms(ms: float) -> None:
    """Async sleep for `ms` milliseconds."""
    if ms > 0:
        import asyncio
        await asyncio.sleep(ms / 1000.0)
