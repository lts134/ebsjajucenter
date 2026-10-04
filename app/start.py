"""실행 진입점. 필요한 모듈을 확인해 없는 것만 설치한 뒤 Streamlit 앱을 띄운다.
사용: python start.py  (또는 app.py를 직접 실행해도 여기로 넘어옴)"""
import sys, subprocess, importlib
from pathlib import Path

APP = Path(__file__).parent / "app.py"
# import 이름 → pip 패키지 스펙
REQUIRED = {
    "streamlit": "streamlit>=1.50",
    "pandas": "pandas>=2.0",
    "openpyxl": "openpyxl>=3.1",
    "anthropic": "anthropic>=1.8",
    "pypdf": "pypdf>=4.0",
    "docx": "python-docx>=1.1",
}

def missing_modules():
    out = []
    for mod, spec in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            out.append(spec)
    return out

def main():
    miss = missing_modules()
    if miss:
        print(f"[설치] 없는 모듈 {len(miss)}개 설치: {', '.join(miss)}")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", *miss])
        if r.returncode != 0:
            print("[오류] 설치 실패. 인터넷 연결 또는 pip 상태를 확인하세요."); sys.exit(1)
        still = missing_modules()
        if still:
            print(f"[오류] 설치 후에도 없음: {still}"); sys.exit(1)
    else:
        print("[확인] 필요한 모듈 모두 설치돼 있음 — 설치 건너뜀")
    # 첫 실행 때 나오는 이메일 입력 프롬프트 건너뛰기(빈 값으로 등록)
    cred = Path.home() / ".streamlit" / "credentials.toml"
    if not cred.exists():
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text('[general]\nemail = ""\n', encoding="utf-8")
    print("[실행] Streamlit 앱 시작 (종료: Ctrl+C)")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(APP)])

if __name__ == "__main__":
    main()
