"""테스트 공통: 앱 폴더를 import 경로에 넣고, 이력 DB를 임시 파일로 돌린다(실제 storage/history.db를 건드리지 않음)."""
import os, sys
from pathlib import Path
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ.setdefault("HISTORY_DB", str(Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / f"ebs_test_{os.getpid()}.db"))
os.environ.pop("ANTHROPIC_API_KEY", None)   # 테스트는 규칙 경로·가짜 클라이언트만 사용
os.environ.setdefault("APP_TODAY", "2026-10-04")   # 연도 없는 날짜 사례(H3)의 기대값이 해가 바뀌어도 유지되게
import pytest

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    import db
    p = tmp_path / "h.db"
    monkeypatch.setattr(db, "DB_PATH", p)
    db._initialized.discard(str(p))
    return db
