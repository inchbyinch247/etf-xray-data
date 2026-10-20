"""국내·미국 결과를 합쳐 앱이 읽는 검색 목록(instruments.json)과 meta.json을 만든다."""
from __future__ import annotations

import datetime as dt

from .common import CACHE, CONFIG, OUT, read_json, write_json


def add_leveraged(items: list[dict], seen: set[str]) -> int:
    """레버리지 ETF는 구성종목이 스왑·선물이라, 기초지수 ETF의 구성종목에 배수를 붙인 파일로 대신한다."""
    made = 0
    for e in read_json(CONFIG / "leveraged.json", {}).get("etfs", []):
        lev = e.get("leverage")
        if not isinstance(lev, (int, float)) or lev <= 0:
            print(f"[publish] {e.get('ticker')}: 배수 {lev!r}는 지원하지 않아 건너뜀 (인버스 미지원)")
            continue
        base = read_json(OUT / "holdings" / f"{e['underlying']}.json")
        if not base:
            print(f"[publish] {e['ticker']}: 기초 ETF {e['underlying']} 구성종목이 없어 건너뜀")
            continue
        write_json(OUT / "holdings" / f"{e['ticker']}.json", {
            **base,
            "ticker": e["ticker"],
            "underlying": e["underlying"],
            "leverage": lev,
            "source": f"{base.get('source', '')} · 기초 ETF {e['underlying']} 구성 × {lev:g}배",
        })
        if e["ticker"] not in seen:
            seen.add(e["ticker"])
            items.append({"ticker": e["ticker"], "name": e["name"], "kind": "ETF", "market": e["market"]})
        made += 1
    return made


def main():
    items, seen = [], set()
    for part in ["instruments_kr.json", "instruments_us.json"]:
        for it in read_json(OUT / part, []):
            if it["ticker"] not in seen:
                seen.add(it["ticker"])
                items.append(it)
    lev = add_leveraged(items, seen)
    write_json(OUT / "instruments.json", items)
    write_json(OUT / "meta.json", {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "us": read_json(CACHE / "us_meta.json", {}),
        "kr": read_json(CACHE / "kr_meta.json", {}),
        "sources": {
            "usHoldings": "SEC Form N-PORT",
            "krHoldings": "각 운용사 공시",
        },
    }, compact=False)
    print(f"[publish] 검색 목록 {len(items)}개 (레버리지 ETF {lev}개 포함)")


if __name__ == "__main__":
    main()
