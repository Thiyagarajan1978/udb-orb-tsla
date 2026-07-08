import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from udb_orb.config import load_config  # noqa: E402

_TZ = "America/New_York"


def build_bars(rows, day="2024-06-03"):
    """rows: list of (hh, mm, open, high, low, close, volume) for a single day."""
    idx, data = [], []
    for hh, mm, o, h, l, c, v in rows:
        idx.append(pd.Timestamp(f"{day} {hh:02d}:{mm:02d}:00", tz=_TZ))
        data.append((o, h, l, c, v))
    df = pd.DataFrame(data, columns=["open", "high", "low", "close", "volume"], index=idx)
    return df


def base_config():
    return load_config()
