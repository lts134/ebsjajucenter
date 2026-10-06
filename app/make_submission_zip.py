"""제출용 ZIP 생성: 앱 소스 + 샘플 데이터 + 템플릿 + 문서(docs/)를 묶는다. 이력 DB·출력물·캐시는 제외.
사용: python make_submission_zip.py  → ../dist/EBS_요구자료대응에이전트_<날짜>.zip"""
import zipfile, datetime as dt
from pathlib import Path

APP = Path(__file__).parent
DIST = APP.parent / "dist"; DIST.mkdir(exist_ok=True)
EXCLUDE_DIRS = {"__pycache__", ".ruff_cache", "out", ".pytest_cache", "dist"}
EXCLUDE_FILES = {"history.db", "history.db-journal", "history.db-wal", "history.db-shm", "shots.db", "apptest.db"}

def wanted(p: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in p.relative_to(APP).parts): return False
    if p.name in EXCLUDE_FILES or p.name.endswith(".broken") or p.suffix in (".pyc",): return False
    return p.is_file()

def main():
    out = DIST / f"EBS_요구자료대응에이전트_{dt.date.today():%Y%m%d}.zip"
    files = sorted(p for p in APP.rglob("*") if wanted(p))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, Path("app") / p.relative_to(APP))
    print(f"{out} ← {len(files)}개 파일, {out.stat().st_size / 1024:.0f} KB")
    return out

if __name__ == "__main__":
    main()
