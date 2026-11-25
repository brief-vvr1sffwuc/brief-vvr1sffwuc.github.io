"""The market-mood sum itself, with only Python's standard library, so GitHub's refresher can run
the same code as the Mac (see mood.py for the rules, and briefing_page.py for the refresher).
"""

from __future__ import annotations

import bisect
import csv
import io
from dataclasses import dataclass

LOOKBACK = 756            # trading days a reading is compared with (about 3 years)
AVERAGE = 200             # the Nasdaq-100's moving average
GREED, FEAR = 80.0, 20.0


class MoodError(Exception):
    pass


@dataclass(frozen=True)
class Mood:
    day: str
    vix: float
    fear: float       # percentile of the VIX, 0-100
    heat: float       # percentile of the Nasdaq-100's stretch above its 200-day average
    score: float

    @property
    def label(self) -> str:
        return "Greed" if self.score >= GREED else "Fear" if self.score <= FEAR else "Normal"


def parse_series(text: str) -> list[tuple[str, float]]:
    """FRED's CSV: a header, then date,value rows; missing days are '.' or empty."""
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if i == 0 or len(row) < 2:
            continue
        try:
            rows.append((row[0].strip(), float(row[1])))
        except ValueError:
            continue
    if not rows or len(rows[0][0]) != 10:
        raise MoodError("FRED's answer isn't the table of dates and values it should be")
    return rows


def parse_cboe(text: str) -> list[tuple[str, float]]:
    """Cboe's daily VIX file: DATE,OPEN,HIGH,LOW,CLOSE with dates as MM/DD/YYYY. Returns
    (YYYY-MM-DD, close) rows, oldest first."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(reader, [])]
    if "DATE" not in header or "CLOSE" not in header:
        raise MoodError("Cboe's answer isn't the VIX table it should be")
    at, close = header.index("DATE"), header.index("CLOSE")
    for row in reader:
        try:
            month, day, year = row[at].strip().split("/")
            rows.append((f"{int(year):04d}-{int(month):02d}-{int(day):02d}", float(row[close])))
        except (ValueError, IndexError):
            continue
    if not rows:
        raise MoodError("Cboe's VIX table is empty")
    return sorted(rows)


def _percentile(earlier: list[float], value: float) -> float:
    """The share (0-100) of the earlier values that are lower than this one."""
    ordered = sorted(earlier)
    return 100.0 * bisect.bisect_left(ordered, value) / len(ordered)


def compute(vix: list[tuple[str, float]], index: list[tuple[str, float]]) -> list[Mood]:
    """The mood on every day both series have a value and enough history."""
    closes = dict(index)
    days = [(d, v) for d, v in vix if d in closes]
    stretch: list[float | None] = []
    window_sum = 0.0
    values = [closes[d] for d, _ in days]
    for i, close in enumerate(values):
        window_sum += close
        if i >= AVERAGE:
            window_sum -= values[i - AVERAGE]
        stretch.append(close / (window_sum / AVERAGE) - 1 if i >= AVERAGE - 1 else None)
    out = []
    for i, (day, v) in enumerate(days):
        if i < LOOKBACK or stretch[i - LOOKBACK] is None:
            continue
        fear = _percentile([x for _, x in days[i - LOOKBACK:i]], v)
        heat = _percentile([s for s in stretch[i - LOOKBACK:i] if s is not None], stretch[i])
        out.append(Mood(day, v, fear, heat, (heat + 100.0 - fear) / 2))
    return out
