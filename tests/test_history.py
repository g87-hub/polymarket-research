import pandas as pd

from pmr import history

from .synthetic import make


def _write(tmp_path, monkeypatch, mispricing=0.0, hours=72):
    monkeypatch.setattr(history, "OUTCOMES", tmp_path)
    monkeypatch.setattr(history, "REPORTS", tmp_path / "rep")
    data, outc = make(hours=hours, step_s=60, seed=5, mispricing=mispricing)
    b = data["books"][data["books"].side == "up"]
    px = pd.DataFrame({"start_epoch": b.start_epoch, "t": b.src_ts.astype(int),
                       "p_up": ((b.best_ask + b.best_bid) / 2).clip(0.001, 0.999)})
    outc.assign(found=True).to_csv(tmp_path / "btc_5m.csv", index=False)
    px.to_csv(tmp_path / "btc_5m_prices.csv", index=False)


def test_efficient_history_finds_nothing(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    rep = history.report()
    assert rep["markets"] > 500 and rep["n_tests"] > 0
    assert not any(r["verdict"].startswith("promising") for r in rep["strategies"].values())
    assert (tmp_path / "rep" / "history_latest.md").read_text().startswith("# How past markets closed")


def test_no_prices_still_reports_outcomes(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, hours=12)
    (tmp_path / "btc_5m_prices.csv").unlink()
    rep = history.report()
    assert "base_rates" in rep and "strategies" not in rep
