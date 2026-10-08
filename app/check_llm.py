"""추출 품질 점검: 샘플 4건 + 실전형 5건을 규칙 기반과 Claude 경로로 각각 추출해 정답표(testcases.py)와 비교한다.
사용: (앱과 같은 폴더에서)  set ANTHROPIC_API_KEY=...  →  python check_llm.py   (다른 공급자: set LLM_PROVIDER=openai + OPENAI_API_KEY, 또는 gemini + GEMINI_API_KEY)
      키가 없으면 규칙 기반만 점검한다. 결과: storage/llm_check_<날짜>.md (+ 콘솔 요약)
채점: must(반드시 잡아야 할 지표·기준일 쌍) 재현율, extra(정답표에 없는 쌍) 수, 제출기한·접수일·요청 주체 일치 여부.
설명·문안 초안 품질은 자동 채점하지 않고 원문을 보고서에 그대로 남겨 담당자가 본다."""
import sys, json, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import extract, normalize, llm
from testcases import CASES

OUT = Path(__file__).parent / "storage"; OUT.mkdir(exist_ok=True)

def pairs(items):
    return [(it.get("indicator"), it.get("base_date")) for it in items]

def score(res: dict, exp: dict) -> dict:
    got = pairs(res.get("items") or [])
    must, may, not_ = exp.get("must", []), exp.get("may", []), exp.get("not", [])
    hit = [p for p in must if p in got]
    extra = [p for p in got if p not in must and p not in may and p[0] is not None]
    bad = [p for p in not_ if p in got]
    null_ok = all(any(k in it.get("item_text", "") and it.get("indicator") is None for it in res.get("items") or []) for k in exp.get("none_ok", []))
    def eq(k, key):
        e = exp.get(k)
        return None if e is None else (res.get(key) == e)
    return {"must_hit": len(hit), "must_total": len(must), "missed": [p for p in must if p not in got], "extra": extra, "bad": bad,
            "null_ok": null_ok, "due_ok": eq("due", "due_date"), "received_ok": eq("received", "received_date"),
            "requester_ok": (exp["requester"] in (res.get("requester") or "")) if exp.get("requester") else None,
            "n_items": len(got)}

def run(path_name: str, fn):
    rows = []
    for c in CASES:
        try:
            res = fn(c["text"])
        except Exception as e:
            res = {"items": [], "_error": f"{type(e).__name__}: {e}"}
        res["items"] = normalize.normalize_items(res.get("items") or [])
        rows.append({"id": c["id"], "res": res, "score": score(res, c["expected"]), "llm": dict(llm.LAST) if path_name != "규칙" else {}})
    return rows

def md_table(rows):
    h = "| 사례 | 필수 재현 | 누락 | 초과 | 오탐 | null 유지 | 기한 | 접수일 | 주체 | 항목 수 |\n|---|---|---|---|---|---|---|---|---|---|\n"
    def yn(v): return "—" if v is None else ("O" if v else "X")
    for r in rows:
        s = r["score"]
        h += (f"| {r['id']} | {s['must_hit']}/{s['must_total']} | {', '.join(f'{a}@{b}' for a, b in s['missed']) or '-'} | "
              f"{', '.join(f'{a}@{b}' for a, b in s['extra']) or '-'} | {', '.join(f'{a}@{b}' for a, b in s['bad']) or '-'} | "
              f"{yn(s['null_ok'])} | {yn(s['due_ok'])} | {yn(s['received_ok'])} | {yn(s['requester_ok'])} | {s['n_items']} |\n")
    tot = sum(r["score"]["must_hit"] for r in rows); den = sum(r["score"]["must_total"] for r in rows)
    ex = sum(len(r["score"]["extra"]) + len(r["score"]["bad"]) for r in rows)
    return h + f"\n합계: 필수 재현 {tot}/{den} ({tot / den * 100:.0f}%), 초과·오탐 {ex}건\n"

def main():
    has = llm.available()
    print(f"[점검] 사례 {len(CASES)}건 · Claude API: {'있음' if has else '없음(규칙 기반만)'}")
    paths = {"규칙": run("규칙", extract.rule_based)}
    if has:
        def via_llm(t):
            r = extract.llm_based(t); return r
        paths["Claude"] = run("Claude", via_llm)
    lines = [f"# 추출 품질 점검 보고 ({dt.datetime.now():%Y-%m-%d %H:%M})", "",
             f"사례 {len(CASES)}건(샘플 4 + 실전형 5, 전부 가상). 모델: {llm.model_label() if has else '—'}", ""]
    for name, rows in paths.items():
        lines += [f"## {name} 경로", "", md_table(rows)]
        if name == "Claude":
            u = llm.usage_summary()
            lines += [f"호출 {u['calls']}회 · 입력 {u['input_tokens']} / 출력 {u['output_tokens']} 토큰 · 평균 {u['avg_latency_s']}초", ""]
    lines += ["## 사례별 추출 결과(원문 대조용)", ""]
    for i, c in enumerate(CASES):
        lines += [f"### {c['id']}", "", "```", c["text"].strip(), "```", ""]
        for name, rows in paths.items():
            r = rows[i]["res"]
            lines += [f"**{name}** — 주체: {r.get('requester')} · 접수 {r.get('received_date')} · 기한 {r.get('due_date')} · 제목 {r.get('title')}"
                      + (f" · 오류: {r['_error']}" if r.get("_error") else ""), ""]
            lines += ["| 항목 원문 | 지표 | 기준일 | 기간 | 단위 |", "|---|---|---|---|---|"]
            for it in r.get("items") or []:
                lines.append(f"| {it.get('item_text')} | {it.get('indicator')} | {it.get('base_date')} | {it.get('period')} | {it.get('unit')} |")
            lines.append("")
    if has:
        lines += ["## 호출 기록", "", "| 시각 | 용도 | 모델 | 지연(초) | 입력 | 출력 | 구조화 출력 | 추정 비용($) |", "|---|---|---|---|---|---|---|---|"]
        lines += [f"| {x['when']} | {x['purpose']} | {x['model']} | {x['latency_s']} | {x['input_tokens']} | {x['output_tokens']} | {'O' if x.get('structured') else '-'} | {x.get('cost_usd')} |" for x in llm.LOG]
        u = llm.usage_summary(); lines += ["", f"합계 추정 비용 ${u['cost_usd']} (공개 단가표 기준, 실제 청구는 콘솔 확인)"]
    suffix = "" if has else "_rules-only"          # 키 없는 점검이 Claude 경로 보고서를 덮어쓰지 않게
    p = OUT / f"llm_check_{dt.date.today()}{suffix}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    for name, rows in paths.items():
        print(f"\n== {name} ==\n" + md_table(rows))
    print("보고서 →", p)
    (OUT / f"llm_check_{dt.date.today()}{suffix}.json").write_text(json.dumps({k: [{"id": r["id"], "score": r["score"], "res": r["res"]} for r in v] for k, v in paths.items()}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

if __name__ == "__main__":
    main()
