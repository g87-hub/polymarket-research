import json

import pytest

from pmr import normalize, research

from .synthetic import make


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    norm, outd, rep, res = (tmp_path / x for x in ("norm", "out", "reports", "research"))
    for d in (norm, outd, rep, res):
        d.mkdir()
    monkeypatch.setattr(normalize, "NORM", norm)
    monkeypatch.setattr(research, "OUTCOMES", outd)
    monkeypatch.setattr(research, "REPORTS", rep)
    monkeypatch.setattr(research, "RESEARCH", res)
    monkeypatch.setattr(research, "REGISTER", res / "register.jsonl")
    monkeypatch.setattr(research, "FROZEN", res / "frozen.json")
    monkeypatch.setattr(research, "TEST_LOCK", res / "test_lock.json")
    data, outc = make(hours=20, step_s=5, seed=3)
    run = norm / "synthetic_run"
    run.mkdir()
    data["markets"].to_csv(run / "markets.csv", index=False)
    data["books"].to_csv(run / "books.csv.gz", index=False)
    data["spot"].to_csv(run / "spot.csv.gz", index=False)
    outc.to_csv(outd / "btc_5m.csv", index=False)
    return tmp_path


def test_end_to_end(sandbox):
    rep = research.research(research.ExecParams(stale_s=6.0))
    assert rep["n_variants_tested"] == len(research.S.default_grid())
    lines = (sandbox / "research" / "register.jsonl").read_text().splitlines()
    assert len(lines) == 2 * rep["n_variants_tested"]  # every variant x (train, validation) logged
    assert rep["enough_data"] is False  # 20h of one combo is deliberately too little
    assert all(v in ("insufficient evidence", "rejected") for v in rep["verdicts"].values())
    assert (sandbox / "reports" / "research_latest.md").exists()

    v = "fair_value|edge_min=0.03,window=(0.2, 0.6)"
    research.freeze(v, research.ExecParams(stale_s=6.0))
    with pytest.raises(SystemExit):
        research.freeze(v)  # cannot silently re-freeze
    lock = research.evaluate_test()
    assert lock["variant"] == v
    with pytest.raises(SystemExit):
        research.evaluate_test()  # untouched test can be used once
    ev = research.paper_evaluate()
    assert ev["passes_rule"] is False  # no post-freeze data
    assert json.loads((sandbox / "reports" / "paper" / "paper_eval.json").read_text())["note"].startswith("all fills")
