"""국내·미국 결과를 합쳐 앱이 읽는 검색 목록(instruments.json)과 meta.json을 만든다."""
from __future__ import annotations

import datetime as dt

from .common import CACHE, OUT, read_json, write_json


def main():
    items, seen = [], set()
    for part in ["instruments_kr.json", "instruments_us.json"]:
        for it in read_json(OUT / part, []):
            if it["ticker"] not in seen:
                seen.add(it["ticker"])
                items.append(it)
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
    print(f"[publish] 검색 목록 {len(items)}개")


if __name__ == "__main__":
    main()
