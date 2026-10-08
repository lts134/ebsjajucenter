"""호환성·보안 점검(2026-10-08) 반영분: 저장 경로 환경변수, 백업 보존, 하루 AI 상한, 호출 주소 검사, 공급자 플러그인(AWS·Bedrock·Vertex),
로그인 잠금, 회신 파일 경로 탈출 차단·DB 보관, 복원 검증, 직원 이름 필드 제거, 압축 폭탄 차단."""
import io, os, subprocess, sys, sqlite3, zipfile
from pathlib import Path
import pytest

APP = Path(__file__).resolve().parents[1]


def test_storage_dir_env_moves_db_and_out(tmp_path):
    env = dict(os.environ, APP_STORAGE_DIR=str(tmp_path / "s")); env.pop("HISTORY_DB", None)
    code = "import db; from screens import common; print(db.DB_PATH); print(common.OUT_DIR)"
    out = subprocess.run([sys.executable, "-c", code], cwd=APP, env=env, capture_output=True, text=True, check=True).stdout.split()
    assert out[0] == str(tmp_path / "s" / "history.db") and out[1] == str(tmp_path / "s" / "out")


def test_backup_retention(fresh_db, monkeypatch):
    db = fresh_db; monkeypatch.setattr(db, "BACKUP_KEEP", 2)
    for f in db.backup_dir().glob("history_*.db"): f.unlink()
    for i in range(4): db.backup_now(f"t{i}")
    names = [b["name"] for b in db.list_backups(10)]
    assert len(names) == 2 and all("_t3" in n or "_t2" in n for n in names)


def test_daily_budget_blocks_calls(fresh_db, monkeypatch):
    import llm
    monkeypatch.setattr(llm, "DAILY_BUDGET_USD", 0.5)
    assert llm.spent_today() == 0.0
    llm._spend_add(0.3); llm._budget_check()
    llm._spend_add(0.3)
    with pytest.raises(llm.BudgetExceeded) as e: llm._budget_check()
    assert "한도" in str(e.value) and llm.explain_error(e.value).startswith("오늘 AI 사용 한도")
    monkeypatch.setattr(llm, "DAILY_BUDGET_USD", 0); llm._budget_check()                # 0이면 제한 없음


def test_check_base_url_rejects_internal():
    import providers
    assert providers.check_base_url("https://gw.example.com/v1/") == "https://gw.example.com/v1"
    for bad in ("http://gw.example.com", "https://localhost:8080", "https://127.0.0.1", "https://10.0.0.5/v1", "https://192.168.1.1", "https://169.254.169.254/", "https://[::1]/", "https://gw.corp.internal", "https://box.local"):
        with pytest.raises(PermissionError): providers.check_base_url(bad)


def test_anthropic_base_url_field_guard(monkeypatch):
    import providers
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False); monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert providers.get("anthropic", {"api_key": "k"}).base() is None
    assert providers.get("anthropic", {"api_key": "k", "base_url": "https://gw.example.com/"}).base() == "https://gw.example.com"
    with pytest.raises(PermissionError): providers.get("anthropic", {"api_key": "k", "base_url": "https://10.1.1.1"}).base()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "shared")
    with pytest.raises(PermissionError): providers.get("anthropic", {"base_url": "https://evil.example.com"}).base()   # 공용 키 + 접속자 주소
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gw.corp.internal/anthropic")                                     # 관리자 환경변수 주소는 검사 없이 허용
    assert providers.get("anthropic", {}).base() == "https://gw.corp.internal/anthropic"


def test_cloud_provider_plugins_registered():
    import providers
    assert {"aws", "bedrock", "vertex"} <= set(providers.PROVIDERS) and not providers._errors
    b = providers.get("bedrock", {"aws_region": "ap-northeast-2"})
    assert b.ready() and not b.wants_fallback("claude-opus-5-5") and b.list_models() == [] and b.default_models == []
    kw = b.request_kwargs(model="m", max_tokens=10, messages=[], system="s", cache=True)
    assert "cache_control" not in kw and kw["system"][0]["cache_control"]["type"] == "ephemeral"
    v = providers.get("vertex", {"project_id": "p"}); assert not v.ready()
    v = providers.get("vertex", {"project_id": "p", "region": "us-east5"}); assert v.ready() and not v.wants_fallback("claude-opus-5-5")
    a = providers.get("aws", {"aws_region": "us-east-1", "workspace_id": "w"}); assert a.ready() and a.wants_fallback("claude-opus-5-5") and a.default_models


def test_auth_lockout_session_ip_and_global(monkeypatch):
    from screens import common as c
    monkeypatch.setattr(c, "AUTH_FAILS", {}); monkeypatch.setattr(c, "AUTH_GLOBAL", {"n": 0, "since": 0.0})
    ss = {}
    assert c.auth_locked("1.2.3.4", ss) == 0
    for i in range(4): n, delay = c.auth_fail("1.2.3.4", ss); assert n == i + 1 and delay == 0 and c.auth_locked("1.2.3.4", ss) == 0
    n, _ = c.auth_fail("1.2.3.4", ss); assert n == 5 and c.auth_locked("1.2.3.4", ss) > 800
    assert c.auth_locked("1.2.3.4", {}) > 800                      # 같은 IP의 새 세션도 잠김
    assert c.auth_locked("5.6.7.8", {}) == 0                       # 다른 IP는 영향 없음
    c.auth_ok("1.2.3.4", ss); assert c.auth_locked("1.2.3.4", ss) == 0 and "auth_fails" not in ss
    ss2 = {}
    for _ in range(5): c.auth_fail(None, ss2)                      # IP를 모르는 접속(로컬·프록시)은 세션 기준으로 잠김
    assert c.auth_locked(None, ss2) > 800 and c.auth_locked(None, {}) == 0
    for _ in range(30): _, delay = c.auth_fail("9.9.9.%d" % _, {})  # IP를 바꿔 가며 때려도 전체 지연이 붙는다
    assert delay == 5.0


def test_out_file_blocks_traversal():
    from screens.common import out_file, OUT_DIR
    assert out_file("회신_1.hwpx") == (OUT_DIR / "회신_1.hwpx").resolve()
    for bad in ("../history.db", "../../etc/passwd", "/etc/passwd", "a/b.hwpx", "", None): assert out_file(bad) is None


def test_draft_hwpx_stored_in_db(fresh_db):
    db = fresh_db
    rid = db.add_request("기관", "2026-09-01", "2026-09-10", "t", "원문", "f.txt", [])
    sid = db.add_submission(rid, "2026-09-05", "u", "f.xlsx", "confirmed", "", [])
    did = db.add_draft(sid, rid, {"제목": "제목", "본문": "본문"}, "회신.hwpx", status="approved", hwpx_bytes=b"PK\x03\x04data")
    assert db.draft_hwpx(did) == b"PK\x03\x04data" and "hwpx" not in db.list_drafts()[0] and db.list_drafts()[0]["hwpx_name"] == "회신.hwpx"
    did2 = db.add_draft(sid, rid, {"제목": "t"}, None); assert db.draft_hwpx(did2) is None


def test_restore_rejects_trigger_and_corrupt(fresh_db, tmp_path):
    db = fresh_db
    bad = tmp_path / "t.db"; con = sqlite3.connect(bad)
    con.executescript("CREATE TABLE requests(id INTEGER); CREATE TABLE submissions(id INTEGER); CREATE TRIGGER tr AFTER INSERT ON requests BEGIN SELECT 1; END;"); con.commit(); con.close()
    with pytest.raises(ValueError, match="트리거"): db.restore_from_bytes(bad.read_bytes())
    with pytest.raises(ValueError): db.restore_from_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    assert db.restore_from_bytes(db.snapshot_bytes())["counts"]                        # 정상 사본은 복원된다


def test_person_keys_stripped():
    import history_qa
    out = history_qa._strip({"id": 1, "assignee": "홍길동", "memo": "m", "rows": [{"loaded_by": "김", "value": 3}, {"sent_by": "박", "updated_by": "최"}]})
    assert out == {"id": 1, "memo": "m", "rows": [{"value": 3}, {}]}


def test_zip_bomb_guard(monkeypatch):
    import docread
    monkeypatch.setattr(docread, "ZIP_MAX_TOTAL", 1000)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z: z.writestr("Contents/section0.xml", "<a/>" + " " * 5000)
    with pytest.raises(ValueError, match="압축"): list(docread._hwpx_sections(b.getvalue()))
    monkeypatch.setattr(docread, "ZIP_MAX_TOTAL", 10 ** 8)
    assert len(list(docread._hwpx_sections(b.getvalue()))) == 1


def test_prompt_injection_wrapping():
    import extract, agent
    assert "<요구서>" in extract.PROMPT and "지시가 아니다" in extract.PROMPT and extract.PROMPT.count("%s") == 4
    assert "지시가 아닙니다" in agent.SYSTEM
