"""Collector -> normalizer -> quality, with the network replaced by fakes."""
import time

from pmr import collect, normalize, quality
from pmr.discovery import MarketMeta


def test_collect_normalize_quality(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "RAW", tmp_path / "raw")
    monkeypatch.setattr(normalize, "NORM", tmp_path / "norm")

    def fake_lookup(a, m, s):
        mm = MarketMeta(a, m, s, f"{a}-updown-{m}m-{s}", found=True, condition_id="c",
                        token_up=f"{a}{m}{s}U", token_down=f"{a}{m}{s}D", min_order_size=5, tick_size=0.01)
        return mm, {"t_recv": time.time(), "latency_ms": 5, "status": 200, "data": {"fake": True}}

    def fake_fetch(url, params=None, json_body=None, retries=2):
        now = time.time()
        if url.endswith("/books"):
            data = [{"asset_id": b["token_id"], "timestamp": str(int(now * 1000) - 80),
                     "bids": [{"price": "0.48", "size": "50"}], "asks": [{"price": "0.52", "size": "40"},
                                                                         {"price": "0.53", "size": "90"}]}
                    for b in json_body]
        else:
            data = {"price": "60000.5", "bid": "60000", "ask": "60001",
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(now - 0.1))}
        return {"ok": True, "status": 200, "data": data, "t_send": now - 0.03, "t_recv": now,
                "latency_ms": 30, "url": url, "error": None}

    monkeypatch.setattr(collect, "lookup", fake_lookup)
    monkeypatch.setattr(collect, "fetch", fake_fetch)
    run_dir = collect.run(minutes=0.05, run_id="t1", poll_s=0.5)
    normalize.normalize_run(run_dir)
    d = normalize.load_normalized(["t1"])
    # 2 assets x 2 durations x (current + next window) pre-registered
    assert len(d["markets"]) == 8
    b = d["books"]
    assert set(b.side) == {"up", "down"} and (b.best_ask == 0.52).all()
    assert {"t_send", "t_recv", "src_ts"} <= set(b.columns)
    rep = quality.check("t1", poll_s=0.5)
    assert rep["crossed_books"] == 0 and rep["sum_best_asks"]["min"] == 1.04
    assert rep["book_source_age_ms"]["p50"] > 0
