"""CUSIP → 미국 티커 변환 (OpenFIGI, 무료). 결과는 cache/figi_cusip.json에 저장해 재사용한다.

API 키가 없어도 되지만 느리다(분당 약 250건). 키를 받으면 GitHub 저장소의
Secrets에 OPENFIGI_API_KEY로 넣으면 훨씬 빨라진다.
"""
from __future__ import annotations

import os
import time

import requests

from .common import CACHE, normalize_us_ticker, read_json, write_json

URL = "https://api.openfigi.com/v3/mapping"
CACHE_FILE = CACHE / "figi_cusip.json"


def map_cusips(cusips: list[str]) -> dict[str, str]:
    cache: dict[str, str | None] = read_json(CACHE_FILE, {})
    todo = [c for c in cusips if c not in cache]
    key = os.environ.get("OPENFIGI_API_KEY")
    batch, pause = (100, 0.25) if key else (10, 2.5)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    if todo:
        print(f"[OpenFIGI] CUSIP {len(todo)}개 조회", flush=True)
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in part]
        for attempt in range(5):
            try:
                r = requests.post(URL, json=jobs, headers=headers, timeout=60)
            except requests.RequestException:
                time.sleep(10)
                continue
            if r.status_code == 429:
                time.sleep(30)
                continue
            break
        else:
            print("  OpenFIGI 응답 없음, 남은 종목은 다음 실행 때 다시 시도", flush=True)
            break
        if r.status_code != 200:
            print(f"  OpenFIGI 오류 {r.status_code}, 다음 실행 때 다시 시도", flush=True)
            break
        for c, res in zip(part, r.json()):
            data = res.get("data") or []
            t = next((normalize_us_ticker(d.get("ticker")) for d in data if d.get("ticker")), None)
            cache[c] = t
        time.sleep(pause)
    write_json(CACHE_FILE, cache, compact=False)
    return {c: cache[c] for c in cusips if cache.get(c)}
