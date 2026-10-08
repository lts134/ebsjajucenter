"""전체 점검을 한 번에: ruff(F/B) → pytest → 규칙 경로 추출 점검(check_llm, 키 없으면 규칙만) → 6단계 AppTest 흐름.
사용: python run_checks.py   (run_checks.bat 더블클릭도 동일). 모두 통과하면 종료 코드 0."""
import os, subprocess, sys, shutil
from pathlib import Path
HERE = Path(__file__).parent
STEPS = [
    ("ruff F/B", [sys.executable, "-m", "ruff", "check", "--select", "F,B", "."]),
    ("pytest", [sys.executable, "-m", "pytest", "-q", "tests"]),
    ("추출 품질 점검(check_llm)", [sys.executable, "check_llm.py"]),
    ("6단계 AppTest 흐름", [sys.executable, str(HERE / "tests" / "flow_apptest.py")]),
]

def main() -> int:
    for mod, spec in (("ruff", "ruff"), ("pytest", "pytest")):
        if shutil.which(mod) is None and subprocess.run([sys.executable, "-m", mod, "--version"], capture_output=True).returncode != 0:
            print(f"[설치] {spec}"); subprocess.run([sys.executable, "-m", "pip", "install", "-q", spec])
    env = dict(os.environ); env.setdefault("HISTORY_DB", str(HERE / "storage" / "check_history.db"))   # 실제 기록 DB 보호
    failed = []
    for name, cmd in STEPS:
        print(f"\n===== {name} =====", flush=True)
        r = subprocess.run(cmd, cwd=HERE, env=env)
        print(f"----- {name}: {'통과' if r.returncode == 0 else '실패'}")
        if r.returncode != 0: failed.append(name)
    for f in list(HERE.glob("storage/check_history.db*")) + list(HERE.glob("storage/llm_check_*_rules-only.*")): f.unlink(missing_ok=True)   # 임시 DB·규칙 전용 보고서 정리
    print("\n결과:", "모두 통과" if not failed else f"실패 {len(failed)}건 — {', '.join(failed)}")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
