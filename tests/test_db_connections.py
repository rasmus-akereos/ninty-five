"""Every database connection is closed when its query is done. On Python 3.14
an unclosed sqlite3 connection stays open until the cyclic garbage collector
runs; the service ran out of file handles (1024) on 2026-11-09."""

import gc
import os

from ninety_five.history import HistoryStore
from ninety_five.store import BetStore


def _open_files(path) -> int:
    n = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            n += os.path.realpath(f"/proc/self/fd/{fd}") == str(path)
        except OSError:
            pass
    return n


def test_history_and_bet_store_close_their_connections(tmp_path):
    db = tmp_path / "h.sqlite"
    history, store = HistoryStore(db), BetStore(db)
    gc.collect()
    gc.disable()
    try:
        before = _open_files(db)
        for _ in range(20):
            history.us_games("NFL", __import__("pandas").Timestamp.now(tz="UTC"))
            history.competitions()
            store.list()
            store.add({"dataset": "E0", "home": f"A{_}", "away": "B", "market": "1", "odds": 2.0, "stake": 1})
        assert _open_files(db) == before
    finally:
        gc.enable()
