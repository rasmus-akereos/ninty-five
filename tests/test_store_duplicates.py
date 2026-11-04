import sqlite3
import threading

import pytest

from ninety_five.store import BetStore, DuplicateBet

BASE = {"dataset": "E0", "home": "Watford", "away": "Lincoln", "market": "1", "odds": 2.0, "stake": 10,
        "match_key": "fd:9"}


@pytest.fixture
def store(tmp_path):
    return BetStore(tmp_path / "b.sqlite")


def test_same_selection_same_match_refused(store):
    store.add(BASE)
    with pytest.raises(DuplicateBet):
        store.add(BASE)
    assert len(store.list()) == 1


def test_other_selection_allowed(store):
    store.add(BASE)
    store.add({**BASE, "market": "X"})
    assert len(store.list()) == 2


def test_allowed_after_settle(store):
    bid = store.add(BASE)
    store.settle(bid, home_goals=1, away_goals=0)
    store.add(BASE)
    assert len(store.list()) == 2


def test_duplicate_across_manual_and_board(store):
    store.add({**BASE, "match_key": None})
    with pytest.raises(DuplicateBet):
        store.add(BASE)


def test_different_match_keys_allowed(store):
    store.add(BASE)
    store.add({**BASE, "match_key": "fd:10"})
    assert len(store.list()) == 2


def test_existing_duplicates_untouched(store):
    with sqlite3.connect(store.path) as c:
        for _ in range(2):
            c.execute("INSERT INTO bets (created, dataset, home, away, market, odds, stake, match_key) "
                      "VALUES ('2026-11-03', 'E0', 'Watford', 'Lincoln', '1', 2.0, 10, 'fd:9')")
    assert len(store.list()) == 2
    with pytest.raises(DuplicateBet):
        store.add(BASE)
    assert len(store.list()) == 2


def test_concurrent_duplicates_store_one(store):
    barrier, ok, refused = threading.Barrier(8), [], []

    def go():
        barrier.wait()
        try:
            ok.append(store.add(BASE))
        except DuplicateBet:
            refused.append(1)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 1 and len(refused) == 7 and len(store.list()) == 1
