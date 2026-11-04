"""Daily automatic fetch inside the server process.

Once a day at 07:00 Danish time the board, results and the country models'
current-season data are fetched. A background thread wakes every hour and
asks `due()`; that also covers a machine that was off or asleep at 07:00
(catch-up when the last fetch is over 24 hours old) and a failed run (still
due at the next hourly check). No OS scheduler is involved, so the same code
runs unchanged on a server. Set NINETY_FIVE_SCHEDULER=0 to switch it off.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

from . import service

RUN_AT = time(7, 0)
TZ = ZoneInfo("Europe/Copenhagen")
MAX_AGE = pd.Timedelta(hours=24)
log = logging.getLogger("ninety_five")


def _ts(v) -> pd.Timestamp | None:
    return None if v is None else pd.Timestamp(v)


def due(last_fetch: pd.Timestamp | None, last_auto: pd.Timestamp | None, now: pd.Timestamp) -> bool:
    """Whether the daily fetch should run now. `last_fetch` is the latest
    successful fetch of any kind, `last_auto` the latest successful daily one."""
    if last_fetch is None or now - last_fetch > MAX_AGE:
        return True
    local = now.tz_convert(TZ)
    today_run = pd.Timestamp.combine(local.date(), RUN_AT).tz_localize(TZ)
    return local >= today_run and (last_auto is None or last_auto < today_run)


def start(history, bets, days: int = 7, wake: float = 3600, stop: threading.Event | None = None) -> threading.Thread | None:
    """Start the daily-fetch thread (None when switched off)."""
    if os.environ.get("NINETY_FIVE_SCHEDULER", "1") == "0":
        return None
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                now = pd.Timestamp.now(tz="UTC")
                if due(_ts(history.last_success()), _ts(history.last_success("auto")), now):
                    out = service.run_refresh(history, bets, days, "auto", refit_countries=True)
                    if out is None:
                        log.info("daily fetch skipped: a refresh is already running")
                    else:
                        log.info("daily fetch done: %s matches", out.get("matches"))
            except Exception as exc:  # retried at the next wake-up
                log.warning("daily fetch failed: %s", exc)
            stop.wait(wake)

    t = threading.Thread(target=loop, name="ninety-five-daily-fetch", daemon=True)
    t.start()
    return t
