"""국내 코드 판별 — 미국 수집이 국내 파일을 지우지 않게 하는 기준. 실행: python -m pytest -q"""
from pipeline.common import is_kr_code


def test_is_kr_code():
    for kr in ["005930", "418660", "0000J0", "0005A0", "0069M0"]:
        assert is_kr_code(kr), kr
    for us in ["QQQ", "TQQQ", "BRK.B", "GOOGL", "ABR.D", "ADAMH", "A00000"]:
        assert not is_kr_code(us), us
