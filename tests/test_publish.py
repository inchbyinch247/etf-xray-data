"""레버리지 ETF를 기초 ETF 구성종목 × 배수로 만드는지 검사한다. 실행: python -m pytest -q"""
import json

from pipeline import publish


def test_leveraged_from_underlying(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "OUT", tmp_path / "docs/v1")
    monkeypatch.setattr(publish, "CONFIG", tmp_path / "config")
    monkeypatch.setattr(publish, "CACHE", tmp_path / "cache")
    (tmp_path / "docs/v1/holdings").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "docs/v1/holdings/QQQ.json").write_text(json.dumps({
        "ticker": "QQQ", "asOf": "2026-06-30", "source": "SEC Form N-PORT",
        "constituents": [{"ticker": "NVDA", "name": "Nvidia", "weight": 9.0}],
    }))
    (tmp_path / "docs/v1/instruments_us.json").write_text(json.dumps([
        {"ticker": "QQQ", "name": "Invesco QQQ", "kind": "ETF", "market": "US"},
    ]))
    (tmp_path / "config/leveraged.json").write_text(json.dumps({"etfs": [
        {"ticker": "TQQQ", "name": "ProShares UltraPro QQQ", "market": "US", "underlying": "QQQ", "leverage": 3},
        {"ticker": "SQQQ", "name": "inverse", "market": "US", "underlying": "QQQ", "leverage": -3},
        {"ticker": "XXX", "name": "no base", "market": "US", "underlying": "NOPE", "leverage": 2},
    ]}))

    publish.main()

    doc = json.loads((tmp_path / "docs/v1/holdings/TQQQ.json").read_text(encoding="utf-8"))
    assert doc["ticker"] == "TQQQ" and doc["leverage"] == 3 and doc["underlying"] == "QQQ"
    assert doc["constituents"] == [{"ticker": "NVDA", "name": "Nvidia", "weight": 9.0}]  # 비중은 그대로, 배수는 앱이 곱한다
    tickers = [i["ticker"] for i in json.loads((tmp_path / "docs/v1/instruments.json").read_text(encoding="utf-8"))]
    assert tickers == ["QQQ", "TQQQ"]  # 인버스·기초 없는 항목은 빠진다
    assert not (tmp_path / "docs/v1/holdings/SQQQ.json").exists()
