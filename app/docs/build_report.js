// [서식5] 프로젝트 개발 보고서를 요강 서식 구조 그대로 DOCX로 만든다. PDF는 LibreOffice로 변환(build_report.sh 참고).
// 사용: node docs/build_report.js [글꼴]   → docs/서식5_프로젝트개발보고서_EBS요구자료대응에이전트.docx
//   글꼴 기본 "맑은 고딕"(Windows 한글/Word용). 리눅스에서 PDF를 만들 때는 "NanumBarunGothic".
const fs = require("fs"); const path = require("path");
const D = require("docx");
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, AlignmentType, BorderStyle, ShadingType,
        ImageRun, PageBreak, Header, Footer, PageNumber, LevelFormat, HeadingLevel, VerticalAlign } = D;

const FONT = process.argv[2] || "맑은 고딕";
const HERE = __dirname; const IMG = path.join(HERE, "img");
const OUT = path.join(HERE, "서식5_프로젝트개발보고서_EBS요구자료대응에이전트.docx");
const BLUE = "256EF4", TEXT = "1E2124", MUTED = "6B7684", HEAD = "DCE9F7", SOFT = "F3F6FA", LINE = "9CA3AF";
const PAGE_W = 11906, MARGIN = 1134, CONTENT = PAGE_W - 2 * MARGIN;   // A4, 2cm 여백 → 9638 DXA

// ---------- 글 조각 ----------
const run = (t, o = {}) => new TextRun({ text: t, font: FONT, size: o.size || 20, bold: o.bold, color: o.color || TEXT, italics: o.italics });
const P = (t, o = {}) => new Paragraph({ alignment: o.align, spacing: { before: o.before ?? 60, after: o.after ?? 60, line: o.line || 300 }, indent: o.indent,
  children: Array.isArray(t) ? t : [run(t, o)] });
const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 160 }, children: [run("■ " + t, { size: 26, bold: true })] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 300, after: 120 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BLUE, space: 2 } }, children: [run(t, { size: 24, bold: true, color: BLUE })] });
const H3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, spacing: { before: 220, after: 80 }, children: [run(t, { size: 21, bold: true })] });
const bullet = (t, lvl = 0) => new Paragraph({ numbering: { reference: "bul", level: lvl }, spacing: { before: 30, after: 30, line: 290 },
  children: typeof t === "string" ? [run(t)] : t });
const note = (t) => P(t, { size: 17, color: MUTED, before: 20, after: 100 });
const emph = (t) => run(t, { bold: true });
const br = () => new Paragraph({ spacing: { before: 0, after: 0 }, children: [run("")] });

// ---------- 표 ----------
const border = { style: BorderStyle.SINGLE, size: 6, color: LINE };
const borders = { top: border, bottom: border, left: border, right: border };
function cell(content, w, o = {}) {
  const paras = (Array.isArray(content) ? content : [content]).map(c => typeof c === "string"
    ? new Paragraph({ alignment: o.align || AlignmentType.LEFT, spacing: { before: 40, after: 40, line: 280 }, children: [run(c, { size: o.size || 18, bold: o.bold, color: o.color })] }) : c);
  return new TableCell({ width: { size: w, type: WidthType.DXA }, borders, verticalAlign: VerticalAlign.CENTER, margins: { top: 60, bottom: 60, left: 100, right: 100 },
    shading: o.fill ? { type: ShadingType.CLEAR, fill: o.fill, color: "auto" } : undefined, columnSpan: o.span, children: paras });
}
const hcell = (t, w, span) => cell(t, w, { fill: HEAD, bold: true, align: AlignmentType.CENTER, span });
const lcell = (t, w, span) => cell(t, w, { fill: SOFT, bold: true, align: AlignmentType.CENTER, span });
function table(widths, rows) {
  return new Table({ width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA }, columnWidths: widths, rows });
}
function grid(widths, header, body, o = {}) {   // header: 글자 배열, body: 행 배열(각 셀 글자 또는 문단 배열)
  const rows = [];
  if (header) rows.push(new TableRow({ tableHeader: true, children: header.map((h, i) => hcell(h, widths[i])) }));
  for (const r of body) rows.push(new TableRow({ children: r.map((c, i) => i === 0 && o.firstLabel ? lcell(c, widths[i]) : cell(c, widths[i], { align: o.center && o.center.includes(i) ? AlignmentType.CENTER : undefined })) }));
  return table(widths, rows);
}
function image(file, widthPx, caption, maxH) {
  const p = fs.existsSync(path.join(IMG, "report", file)) ? path.join(IMG, "report", file) : path.join(IMG, file); const buf = fs.readFileSync(p);
  const sz = pngSize(buf); let h = Math.round(widthPx * sz.h / sz.w);
  if (maxH && h > maxH) { widthPx = Math.round(widthPx * maxH / h); h = maxH; }
  const out = [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 40 }, children: [new ImageRun({ type: "png", data: buf, transformation: { width: widthPx, height: h } })] })];
  if (caption) out.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 160 }, children: [run(caption, { size: 17, color: MUTED })] }));
  return out;
}
function pngSize(buf) { return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) }; }
const box = (c) => c === true ? "■" : "☐";

// ---------- 내용 ----------
const URL = "https://ebs-request-agent-659902904913.asia-northeast3.run.app";
const children = [];
children.push(new Paragraph({ spacing: { before: 0, after: 200 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: TEXT, space: 4 } },
  children: [run("[서식5] 프로젝트 개발 보고서", { size: 32, bold: true })] }));
children.push(P([run("2026 EBS AI Innovation Challenge · EBS AI 에이전트 개발 해커톤 대회 · 트랙1(Now Agent)", { size: 18, color: MUTED })], { after: 200 }));

// 참가 부문
children.push(H1("참가 부문"));
children.push(table([1900, 3869, 3869], [
  new TableRow({ children: [lcell("신청 트랙", 1900), cell(`${box(true)} 트랙1 (Now Agent)`, 3869), cell(`${box(false)} 트랙2 (Next Agent)`, 3869)] }),
  new TableRow({ children: [lcell("참가 구분", 1900), cell(`${box(true)} 개인`, 3869), cell(`${box(false)} 팀(팀원 수 :   명)`, 3869)] }),
  new TableRow({ children: [lcell("팀 명", 1900), cell("(개인 참가 — 해당 없음)", 7738, { span: 2, color: MUTED })] }),
]));

// 참여자 정보
children.push(H1("참여자 정보"));
children.push(grid([1700, 3200, 1900, 2838], ["구 분", "소속 부서", "직 급", "성 명"], [
  ["본인 / 팀장", "지역교육협력부", "[확인 필요]", "이택수"],
  ["팀원 1", "", "", ""], ["팀원 2", "", "", ""], ["팀원 3", "", "", ""], ["팀원 4", "", "", ""],
], { firstLabel: true, center: [1, 2, 3] }));
children.push(note("※ 개인 참가의 경우 「본인/팀장」 1행만 작성하며, 팀원란은 공란으로 둡니다."));

// 프로젝트 개요
children.push(H1("프로젝트 개요"));
children.push(grid([1700, 6338, 1600], ["구 분", "내 용", "비 고"], [
  ["과 제 명", "EBS 대외 요구자료 대응 에이전트", "트랙1"],
  ["프로젝트 개요\n(간략히 설명)".replace("\n", " "), [
    P("의원실·감사·교육부·언론에서 오는 자료 요구에 대해 \"EBS가 언제, 누구에게, 어떤 근거로 답했는지\"를 기억하고, 다음 답변의 수치와 문서를 제출 전에 검증하는 담당자 보조 도구.", { size: 18, before: 20, after: 20 }),
    P("① 요구서(HWP·HWPX·PDF·DOCX)를 읽어 항목·지표·기준일을 뽑고 과거 제출 이력을 자동으로 붙임 → ② 이번 집계값을 지난번 보낸 값과 대조(코드)하고 차이 사유 문구 후보를 근거와 함께 제시 → ③ 확정 수치만으로 공문체 회신 초안·예상 후속 질문·HWPX 출력 → 팀장 검토·승인 → 전 과정이 이력으로 남고 자연어로 조회.", { size: 18, before: 20, after: 20 }),
  ], "실제 작동하는 웹 프로토타입"],
  ["개발 유형", "웹 프로토타입(AI 에이전트). 부서 공용 서버(Google Cloud Run) 접속형 + PC 설치형(동일 코드) 겸용", ""],
  ["활용 AI 도구 및 기술 스택", [
    P("· AI: Anthropic Claude API(claude-sonnet-4-6 기본, 설정에서 변경), 구조화 출력(JSON 스키마), 도구 호출(읽기 전용 조회 7종)", { size: 18, before: 20, after: 10 }),
    P("· 구현: Python 3.11, Streamlit 1.65, pandas 3, SQLite, openpyxl, pypdf, python-docx, python-hwpx(HWP→HWPX 변환), 자체 HWPX 생성 모듈", { size: 18, before: 10, after: 10 }),
    P("· 운영: Docker, Google Cloud Run(요청 기반 과금), Litestream→Cloud Storage(이력 실시간 복제), Playwright(화면 검증·시연 녹화), pytest·ruff", { size: 18, before: 10, after: 10 }),
    P("· 개발 보조: Claude Code(코드 작성·검토·문서화)", { size: 18, before: 10, after: 20 }),
  ], ""],
  ["사내 데이터 활용 여부", [
    P(`${box(false)} 미활용      ${box(true)} 활용(활용 데이터 종류: 과거 회신 공문 HWP 1건 — 표 추출 동작 확인에만 사용)`, { size: 18, before: 20, after: 10 }),
    P("※ 개발·시연 데이터는 전부 가상(센터A~L, 임의 수치)이며, 사내 자료는 외부 AI 서비스에 입력하지 않았음(서식4 준수). 실 HWP 파일은 PC 안에서만 처리하고 결과물·저장소에 포함하지 않음.", { size: 17, color: MUTED, before: 10, after: 20 }),
  ], ""],
], { firstLabel: true }));

children.push(new Paragraph({ spacing: { before: 400, after: 200 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: TEXT, space: 4 } },
  children: [run("■ 프로젝트 개발 내용", { size: 28, bold: true })] }));

// 1. 과제 개요 및 문제 정의
children.push(H2("1. 과제 개요 및 문제 정의"));
children.push(H3("1-1. 업무 상황"));
children.push(P("EBS는 국정감사·감사·교육부·언론으로부터 수시로 자료 요구를 받는다. 자기주도학습센터 운영 부서의 경우 '○월 ○일 기준 센터별 등원율', '등록 학생 수', '예산 집행률' 같은 집계 수치를 요구 주체와 시점만 바꿔 반복해서 요구받는다. 요구서는 한글(HWP) 공문으로 오고, 답변은 엑셀 집계와 한글 회신 공문으로 나간다."));
children.push(P("지금은 이 과정이 담당자 개인의 기억과 메일함·공유폴더에 흩어진 파일에 의존한다. 아래 네 가지가 반복되는 문제다."));
children.push(grid([3400, 6238], ["현장 상황", "결과"], [
  ["같은 지표를 시기·요구 주체만 바꿔 반복 요구", "과거 제출값을 찾는 데 시간이 들고, 담당자가 바뀌면 이력이 끊긴다"],
  ["원자료가 사후 보정되어 같은 기준일 수치가 제출 시점마다 달라짐", "의원실과 교육부에 낸 수치가 서로 달라 '왜 다른가'라는 추가 요구가 생긴다"],
  ["회신 문안을 매번 새로 작성", "항목 누락·기준일 오기·과장 표현의 위험"],
  ["제출 근거(지표 정의·집계기간·추출시점·원자료 버전)가 파일 밖에 남지 않음", "사후 소명이 어렵고, 같은 질문에 같은 답을 보장하지 못한다"],
]));
children.push(H3("1-2. 기존 방식의 한계"));
children.push(bullet([emph("찾기: "), run("과거 회신은 메일·공유폴더·개인 PC에 흩어져 있어 '그때 뭐라고 냈지'를 확인하려면 사람에게 물어야 한다.")]));
children.push(bullet([emph("맞추기: "), run("지난번 값과 이번 값을 엑셀에서 눈으로 비교한다. 차이가 나도 원인(추출 시점·버전 차이)이 기록으로 남지 않는다.")]));
children.push(bullet([emph("쓰기: "), run("회신 공문은 매번 백지에서 쓰고, 어떤 항목이 빠졌는지는 사람이 다시 센다.")]));
children.push(bullet([emph("범용 챗봇의 한계: "), run("ChatGPT·Claude 같은 범용 도구에 요구서를 넣으면 문안은 나오지만, 부서가 과거에 낸 값과 사유를 모른다. 수치를 계산하거나 추정해 버릴 위험도 있어 공공기관 회신에 그대로 쓸 수 없다.")]));
children.push(H3("1-3. 문제의 크기(외부 근거)"));
children.push(P("아래는 공개 자료의 요약으로, 대상이 EBS가 아닌 공무원 집단임을 명시한다. EBS 자체의 연간 요구자료 건수는 내부 집계로 보완이 필요하다 [확인 필요]."));
children.push(bullet("공무원노동조합총연맹 설문(조합원 884명): 국가직·소방직의 35%가 이미 제출한 자료와 유사한 자료를 7회 이상 요구받았고, 광역시·도 응답자의 59%가 3회 이상 반복 요구를 경험. 응답자 80% 이상이 제출 범위가 불분명하다고 답함(법률저널 2026-08-27·09-04 보도 요약, 원문 확인 필요)."));
children.push(bullet("보건복지부·심사평가원이 국정감사 자료에서 엑셀 셀 참조 오류로 잘못된 통계를 제출해 의원실이 문제를 제기한 사례(이투데이 보도 요약). 데이터는 맞지만 모수 정의가 달라 결과적으로 잘못 읽힌 사례(이데일리 보도 요약)."));
children.push(P("요약하면 문제는 '문안을 못 써서'가 아니라 '부서의 기억이 없어서' 생긴다. 이 과제는 그 기억을 구조화해 저장하고, 다음 답변을 그 기억에 비추어 검증하는 도구를 만드는 것이다."));

// 2. 주요 내용 및 차별성
children.push(H2("2. 주요 내용 및 차별성"));
children.push(H3("2-1. 핵심 개념 — '부서의 기억'을 가진 검증 도우미"));
children.push(P("요구서 한 건이 들어오면 요구 → 항목 → 그때 보낸 값(출처·근거 포함) → 차이 사유 → 회신 초안 → 승인을 한 줄의 기록으로 남긴다. 새 요구서가 오면 이 기록에서 같은 지표·같은 기준일을 찾아 '언제 누구에게 얼마로 답했는지'를 자동으로 붙이고, 이번 수치가 그때와 다르면 제출 전에 표시한다."));
children.push(...image("흐름도_보고서.png", 640, "그림 1. 업무 흐름과 역할 분담"));
children.push(H3("2-2. AI의 역할 경계(설계 원칙)"));
children.push(P("AI를 '글 → 구조, 구조 → 글' 변환에만 쓰지 않고, 언어모델이 잘하는 기억·예측·연결에 쓴다. 대신 수치에는 손대지 않는다."));
children.push(grid([2200, 2000, 5438], ["AI 핵심 역할", "어디서", "근거 제한"], [
  ["기억: 과거에 같은 차이를 어떻게 설명했는지 끌어와 문구 후보 제시", "2단계", "대조 단서 + 저장된 차이 사유 기록만. 원인 추측 금지"],
  ["예측: 이 회신을 받은 요구 주체의 후속 질문과 준비할 자료", "3단계", "요구 항목·확정 수치·대조 결과·타 기관 제출 이력·초안. 수치 해석 금지"],
  ["연결: 자연어 질문을 조회 도구 호출로 바꿔 이력에서 답을 찾고 근거 레코드 제시", "이력에 묻기", "읽기 전용 도구 결과만. 평균·증감 가공 금지"],
  ["변환(보조): 요구서 구조화, 공문체 초안, 초안 점검", "등록·1단계·3단계", "사전에 있는 지표명만, 확정 수치만"],
]));
children.push(br());
children.push(grid([4819, 4819], ["AI가 하는 것", "AI가 하지 않는 것"], [
  ["요구서에서 항목·지표·기준일 추출(사전에 있는 지표명만)", "수치 계산·요약·추정(평균·증감도 계산하지 않음)"],
  ["확정 수치·입력된 사유만으로 문안 작성", "차이 사유 추정"],
  ["초안의 누락·불일치·단정 표현 지적", "초안 자동 수정, 최종 판단"],
  ["이력 조회 도구를 골라 호출하고 결과를 근거로 답변", "원자료(개인 단위 출결 등) 접근"],
]));
children.push(P("구현 장치: 지표명은 JSON 스키마의 열거형으로 사전 값만 허용해 지어낸 지표는 형식 단계에서 차단하고, 사전에 없는 이름은 코드가 다시 걸러 null로 둔다. 데이터가 없는 항목은 '[확인 필요]'로 남긴다. 대조는 pandas만 사용한다. 승인 없이는 확정되지 않는다.", { before: 120 }));
children.push(H3("2-3. 기존 방식·범용 도구 대비 차별성"));
children.push(grid([2200, 3700, 3738], ["비교 항목", "기존(엑셀·메일·범용 챗봇)", "이 에이전트"], [
  ["과거 제출값 찾기", "사람에게 묻거나 메일함 검색", "요구서를 올리면 같은 지표·기준일의 과거 제출값과 근거가 자동으로 붙음"],
  ["수치 검증", "눈으로 비교, 기록 없음", "코드가 대조하고 차이 센터·단서(추출시점·버전 변경)를 표시, 점검표 엑셀 출력"],
  ["차이 사유", "매번 새로 씀", "과거에 쓴 사유와 단서를 근거로 후보 제시, 담당자가 선택·입력"],
  ["회신 문안", "백지에서 작성", "확정 수치·사유만으로 공문체 초안, 빠진 항목 검사, 부서 한글 서식(HWPX)으로 출력"],
  ["입력 서식", "집계 엑셀 전제", "집계 엑셀뿐 아니라 실적표 서식 그대로, 과거 회신 공문(HWP·HWPX·DOCX·PDF)의 표, 직접 입력. 여러 출처를 한 제출본으로 묶음"],
  ["보안", "범용 챗봇에 원문 그대로 입력", "연락처 패턴 마스킹·직원 이름 제거 후 전송, 센터 단위 집계값만 전달, 키는 접속자 세션에만 보관"],
  ["기록", "개인 PC", "요구→값→사유→초안→승인이 DB에 남고 자연어로 조회"],
]));

// 3. 프로토타입 구현 및 구동 내용
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(H2("3. 프로토타입 구현 및 구동 내용"));
children.push(H3("3-1. 결과물 형태"));
children.push(grid([2200, 7438], null, [
  ["형태", "웹 애플리케이션(Streamlit). 브라우저로 접속하며 설치가 필요 없음"],
  ["접속", `부서 공용 서버: ${URL} (접속 비밀번호는 제출 메일에 별도 기재). Google Cloud Run에 배포되어 요청이 있을 때만 인스턴스가 켜지고, 이력 DB는 Cloud Storage에 1초 간격으로 복제되어 재시작 후에도 유지`],
  ["설치형", "같은 코드를 PC에서 run.bat으로 실행 가능(여러 사람이 쓰면 run_server.bat). 소스 코드 ZIP과 GitHub 저장소 제출"],
  ["AI 연결", "접속자가 설정 화면에 자기 Claude API 키를 넣어 사용(브라우저 세션에만 보관, 서버 저장 없음). 키가 없으면 전 기능이 규칙 기반으로 동작"],
  ["규모", "Python 약 3,500줄(업무 모듈 17개), 단위 테스트 85건, 화면 흐름 자동 검증(AppTest·Playwright)"],
], { firstLabel: true }));
children.push(H3("3-2. 적용 AI 기술"));
children.push(bullet([emph("구조화 출력: "), run("요구서 추출·지표 분류·열 구성 제안은 JSON 스키마를 지정해 받는다. 지표명은 열거형이라 사전 밖 값이 들어올 수 없고, 형식 오류 재요청이 0회였다. 스키마를 지원하지 않는 모델이면 텍스트 파싱으로 자동 대체한다.")]));
children.push(bullet([emph("도구 호출(이력에 묻기): "), run("읽기 전용 조회 도구 7종(요구서 검색, 제출값 조회, 사유 조회 등)을 모델이 골라 호출하고, 조회된 레코드만 근거로 답한다. 호출 내역을 화면에 그대로 보여 준다.")]));
children.push(bullet([emph("견고성: "), run("출력이 잘리면 출력 한도를 올려 자동 재시도, 모델이 없으면 후보 순으로 대체, 429·5xx 재시도, 호출마다 모델·지연·토큰·추정 비용 기록. 공급자 계층을 분리해 파일 하나로 다른 LLM 백엔드를 붙일 수 있다.")]));
children.push(bullet([emph("입력 안전: "), run("전화·이메일·주민번호·계좌 패턴은 마스킹한 뒤 전송하고, 이력 질의 결과에서는 직원 이름 필드를 제거한다. 개인 단위 원자료는 다루지 않는다.")]));

children.push(H3("3-3. 화면과 기능(구동 화면)"));
children.push(P("화면은 홈 → 새 요구서 처리 3단계(읽기 → 수치 맞춰 보기 → 회신 초안) → 검토·승인이 주 흐름이고, 과거 답변 등록·이력 조회·현황·이력에 묻기·설정이 보조 화면이다. 색·글자 크기는 대한민국 디자인 시스템(KRDS) 토큰을 따르고, 화면마다 주된 행동 버튼을 하나만 둔다. 아래 캡처는 가상 데이터(센터A~L)로 규칙 기반 경로를 돌린 것이다."));
const shots = [
  ["00_홈.png", "홈 — 할 일 세 가지와 현황(등록 요구서·진행 중·기한 3일 이내·검토 대기)"],
  ["01_과거자료등록.png", "과거 답변 등록 — 예전 요구서를 읽고(왼쪽), 그때 보낸 값을 집계 엑셀·실적표 서식·회신 공문의 표·직접 입력 중 어느 것으로든 넣는다(오른쪽). 제목 행·병합 머리글·합계 행이 있는 가로형 실적표를 '센터 열·값 열·기준일'만 확인해 48건 값으로 변환"],
  ["02_새요구서분석.png", "1단계 요구서 읽기 — 항목·지표·기준일 추출, 비슷한 과거 요구서, 항목별 '전에 낸 적이 있는지'(7월 의원실 제출값 12건과 근거가 붙음), 데이터 출처·담당 제안"],
  ["03_수치대조_점검표.png", "2단계 수치 맞춰 보기 — 지난번 값과 대조(짝 12·차이 3·일치 9), 차이 행의 단서, 사유 문구 후보(근거 표시)와 담당자 입력, 제출 전 점검표, 개인정보 패턴 검사"],
  ["04_회신초안_HWPX.png", "3단계 회신 초안 — 확정 수치·사유만으로 쓴 공문체 초안, 빠진 항목 검사([확인 필요] 표시), 예상 후속 질문과 준비 자료, HWPX 출력·제출 묶음 ZIP·팀장 검토 요청"],
  ["05_검토승인.png", "검토·승인 — 검토 대기 초안, 수치·사유·초안 확인 후 승인·반려·의견 기록"],
  ["06_이력조회.png", "이력 조회 — 요구 → 항목 → 보낸 값 → 사유 → 초안 → 승인을 한 줄로, 키워드 검색"],
  ["07_현황통계.png", "현황 — D-day·상태, 관리대장 엑셀, 요청 주체별 건수, 반복 요구 지표(사전 산출 후보)"],
  ["08_이력에묻기.png", "이력에 묻기 — \"감사실에 등원율 어떻게 냈지?\"에 조회 도구 호출 내역과 근거 레코드를 붙여 답함"],
  ["09_설정.png", "설정 — AI 연결(공급자·키·모델·연결 테스트·호출 기록과 추정 비용), 서식·사전, 시연·초기화"],
];
// 한 쪽에 들어가는 만큼(약 870px) 묶어 배치하고, 넘치면 쪽을 나눈다. 세로가 긴 캡처는 높이 760px에 맞춰 줄인다.
let used = 0;
shots.forEach(([f, c], i) => {
  const buf = fs.readFileSync(fs.existsSync(path.join(IMG, "report", f)) ? path.join(IMG, "report", f) : path.join(IMG, f)); const sz = pngSize(buf);
  let h = Math.round(620 * sz.h / sz.w); if (h > 760) h = 760;
  const need = h + 70;
  if (used > 0 && used + need > 880) { children.push(new Paragraph({ children: [new PageBreak()] })); used = 0; }
  children.push(...image(f, 620, `그림 ${i + 2}. ${c}`, 760)); used += need;
});

children.push(H3("3-4. 구동 방식(담당자 사용 순서)"));
children.push(grid([1200, 2600, 5838], ["순서", "화면", "담당자가 하는 일 / 에이전트가 하는 일"], [
  ["0", "과거 답변 등록(최초 1회·수시)", "예전 요구서와 그때 보낸 값을 넣는다. 집계 엑셀, 실적표 서식, 회신 공문(HWP 자동 변환)의 표, 직접 입력 중 편한 것으로. 여러 파일·여러 표를 한 제출본으로 묶을 수 있다 / 표 구조를 읽어 열 확인만 받고 저장"],
  ["1", "새 요구서 처리 → 1단계", "요구서 파일을 올리고 '요구 항목 읽기' / 항목·지표·기준일 추출, 유사 과거 요구서, 항목별 과거 제출값·근거, 데이터 출처·담당 제안"],
  ["2", "2단계", "이번 집계값 파일을 올리고 지난번 답변을 고른다. 차이 행의 사유를 후보에서 고르거나 적고 확정 / 같은 지표·센터·기준일끼리 대조, 단서, 사유 후보, 점검표, 개인정보 검사"],
  ["3", "3단계", "'초안 만들기' → 문안 수정 → 'AI로 한 번 더 점검' → '예상 질문 보기' → 'HWPX 받기' → '팀장 검토 요청' / 초안·누락 검사·점검·후속 질문·HWPX 생성·제출 묶음 ZIP"],
  ["4", "검토·승인", "팀장이 승인·반려 / 승인 기록 저장, 확정 제출본 표시"],
  ["5", "이력 조회·현황·이력에 묻기", "조회·질문 / 한 줄 이력, D-day·반복 지표, 자연어 질의응답(근거 레코드 명시)"],
], { center: [0] }));

children.push(H3("3-5. 아키텍처"));
children.push(grid([2400, 7238], null, [
  ["화면", "Streamlit 단일 앱(app.py) + 디자인 모듈(ui.py). 접속 비밀번호 잠금(APP_PASSWORD), 접속자별 세션 상태"],
  ["AI 계층", "llm.py(세션별 설정, 구조화 출력, 모델 대체, 잘림 재시도, 비용 기록) ← providers.py(공급자 인터페이스: Anthropic, 사용 안 함, 플러그인 자동 발견)"],
  ["업무 모듈", "extract(요구서 추출) · normalize(지표 사전) · search(문자 n-gram 유사도, 외부 API 없음) · suggest(데이터 카탈로그) · compare(대조·점검표) · assist(사유 후보·후속 질문) · history_qa(도구 호출 질의) · draft(초안·충족 검사) · pii(개인정보 패턴)"],
  ["문서 입출력", "docread(HWPX·DOCX·PDF 본문과 표, HWP 5.0 → HWPX 변환) · tabular(표 구조 분석: 제목 행·병합 머리글·가로/세로·합계·기준일 힌트, 여러 출처 묶기) · hwpx_out(서식 HWPX 자리표시자 치환·표 행 복제)"],
  ["저장", "SQLite 1파일(requests, items, submissions, submission_values(출처 포함), diff_reasons, drafts, reviews). 업로드 원본은 저장하지 않고 추출한 값만 저장"],
  ["운영", "Dockerfile → Cloud Run(최소 0·최대 1 인스턴스, 세션 고정) + Litestream으로 DB를 Cloud Storage에 실시간 복제·시작 시 복원. 갱신은 update_cloud.ps1 한 줄(git pull → 빌드 → 배포)"],
  ["외부로 나가는 데이터", "요구서 문안(연락처 패턴 마스킹), 확정 집계값(센터 단위), 담당자 입력 사유, 산출 근거 메타, 이력 질의의 질문과 조회 레코드(직원 이름 제거). 개인 단위 원자료는 전달하지 않음"],
], { firstLabel: true }));

children.push(H3("3-6. 검증 결과(실측)"));
children.push(grid([2400, 4600, 2638], ["항목", "결과", "비고"], [
  ["요구서 추출 품질(Claude 경로)", "실전형 5건 포함 9사례에서 필수 쌍 25/25, 초과·오탐 0 (claude-sonnet-4-6, 2026-09-28·10-04 두 차례). 평균 지연 4.6초", "구조화 출력 적용 후 재확인. storage/llm_check_2026-10-04.md"],
  ["요구서 추출 품질(규칙 경로)", "같은 9사례 25/25", "규칙을 사례에 맞춰 조정했으므로 참고치"],
  ["AI 보조 3종 실호출", "사유 후보: 전부 '대조 단서' 또는 '과거 입력 사유(날짜·기관·센터)'를 인용, 원인 추측 문구 0 / 후속 질문 1순위 '타 기관 제출값과의 차이' / 이력 질의 4건 모두 요구번호·제출본·제출일 명시, 없는 이력은 '없음'", "2026-10-04 실측. docs/실측_기록_2026-10-04.md"],
  ["호출 성능·비용", "요구서 1건 추출 2.6~9.6초, 약 $0.011. 시연 1회(14회 호출) 추정 $0.21. 초안 12초, 추가 점검 4초, 사유 후보 15초, 후속 질문 23~28초", "공개 단가표 기준 추정, 실제 청구는 콘솔 확인 [확인 필요]"],
  ["실제 문서 서식", "과거 회신 공문 HWP(5.0) 1건을 자동 변환해 표 4개를 추출하고 센터×지표 값으로 변환 성공. 제목 행·병합 머리글·합계 행이 있는 가로형 실적표 48건 변환 일치", "실파일은 PC 안에서만 처리, 저장소 미포함"],
  ["단위 테스트", "pytest 85건 통과(추출·정규화·대조·개인정보·HWPX·초안·LLM 가짜 클라이언트·DB·검색·세션 격리·공급자 플러그인·표 구조·HWP 변환·잘림 재시도 등)", "run_checks.py로 ruff → pytest → 추출 점검 → 화면 흐름 한 번에"],
  ["화면 흐름", "홈 → 과거 답변 등록(여러 출처 묶기 포함) → 1·2·3단계 → 승인 → 이력·현황·이력에 묻기·설정까지 자동 구동 오류 없음. 실제 브라우저(Playwright) 구동·캡처", "2026-10-07"],
  ["HWPX 출력", "12행 표 자동 확장, XML 정형성, 자리표시자 잔존 0, 쪼개진 run 치환", "2026-10-04"],
  ["클라우드 배포", "Cloud Run 배포 성공(2026-10-07), 복제 → DB 삭제 → 복원 주기에서 요구서·값 복원 일치 확인", "서울 리전"],
  ["독립 코드·보안 리뷰", "코드 리뷰 결함 15건 중 14건 수정·회귀 테스트화, 1건은 세션별 자격증명 구조로 해소. 보안 리뷰 High 0, Medium 2·Low 1 전부 조치(엑셀 수식 주입 차단, localhost 바인딩, 마스킹 범위 확대)", "2026-10-04"],
]));

// 4. 기대효과 및 향후 발전 가능성
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(H2("4. 기대효과 및 향후 발전 가능성"));
children.push(H3("4-1. 정량 효과"));
children.push(P("아래 측정값은 프로토타입에서 실측한 것이고, 시간 절감 추정은 담당자 1인의 현재 소요 시간을 넣어 계산하도록 식으로 둔다. 현재 소요 시간은 부서 실측으로 채워야 한다 [확인 필요]."));
children.push(grid([3000, 3300, 3338], ["작업", "현재(담당자 기준, 입력 필요)", "에이전트 적용 시(실측)"], [
  ["과거 제출값 찾기", "메일·폴더 검색, 전임자 문의: [확인 필요] 분", "요구서를 올리면 자동으로 붙음: 추출 약 5초 + 확인"],
  ["지난번 값과 대조", "엑셀 눈대중 비교: [확인 필요] 분", "대조·단서·점검표 즉시(코드), 차이 행만 확인"],
  ["차이 사유 작성", "매번 새로 작성: [확인 필요] 분", "근거 있는 후보에서 선택 또는 입력(후보 생성 약 15초)"],
  ["회신 공문 작성", "백지 작성·형식 맞추기: [확인 필요] 분", "초안 약 12초 + 수정, HWPX 서식 자동 출력"],
  ["사후 소명·재질의 대응", "파일을 다시 찾아 재구성", "이력 조회·이력에 묻기로 즉시(6~25초)"],
]));
children.push(P("비용은 요구서 1건 처리(추출·초안·점검·후속 질문 포함) 기준 약 $0.1~0.2 수준으로 추정되며, 서버는 Cloud Run 요청 기반 과금으로 부서 규모 사용량에서는 무료 범위 안이거나 월 수천 원대다(공개 요금표 기준 추정, 실제 청구는 확인 필요).", { before: 120 }));
children.push(H3("4-2. 정성 효과"));
children.push(bullet([emph("일관성: "), run("같은 기준일·같은 지표에 기관마다 다른 수치를 내는 일을 제출 전에 잡는다. 차이가 정당하면 그 사유가 기록으로 남아 다음에 재사용된다.")]));
children.push(bullet([emph("인수인계: "), run("담당자가 바뀌어도 '7월에 의원실에 이 값으로 답했다'와 그 근거(정의·집계기간·추출시점·원자료 버전)가 남는다.")]));
children.push(bullet([emph("대응 속도와 예측: "), run("반복 요구 지표를 현황에서 식별해 미리 산출하고, 예상 후속 질문으로 다음 요구에 대비한다.")]));
children.push(bullet([emph("통제된 AI 사용: "), run("담당자가 범용 챗봇에 원문을 붙여 넣는 대신, 마스킹과 범위 제한이 걸린 경로로 AI를 쓰게 된다. 국정원 「생성형 AI 활용 보안 가이드라인」의 '비공개·개인정보 입력 금지, 생성물 재검증' 원칙과 사내 서식4 기준에 맞춘 설계다.")]));
children.push(H3("4-3. 사내 확산 가능성"));
children.push(P("요구자료 대응은 부서마다 지표만 다를 뿐 '요구서 → 집계값 → 회신 공문 → 사후 소명' 구조가 같다. 다른 부서에 적용할 때 바꾸는 것은 세 가지뿐이다."));
children.push(grid([2600, 7038], ["바꾸는 것", "방법"], [
  ["지표 사전", "normalize.py의 CANON에 부서 지표명과 동의어를 추가(현재 7개). 추출·검색·제안·충족 검사·AI 스키마가 모두 이 사전을 씀"],
  ["데이터 카탈로그", "suggest.py의 CATALOG에 지표별 데이터 출처·담당 기입"],
  ["회신 서식", "부서 회신 공문 HWPX에 {{수신}} {{본문}} {{row.center}} 같은 자리표시자만 넣어 templates/에 둠"],
], { firstLabel: true }));
children.push(P("확산 단계(안): ① 지역교육협력부 등원율·센터 현황 지표로 실데이터 운영(과거 회신 공문을 '과거 답변 등록'으로 소급 입력) → ② 같은 Cloud Run 인스턴스에 타 부서 지표 사전 추가 → ③ 전사 공통 '요구자료 기억'으로 운영하며 사람별 로그인(IAP)과 사내 LLM 게이트웨이 연결. 공급자 계층이 분리되어 있어 사내 지정 API로의 전환은 파일 하나 추가로 가능하다.", { before: 120 }));
children.push(H3("4-4. 한계와 계획"));
children.push(grid([4200, 5438], ["한계", "계획"], [
  ["규칙 경로는 정형 서식(줄머리 번호·'기준' 표기)에 의존", "실제 운영은 Claude 경로. 실제 요구서를 testcases.py에 추가해 재채점(check_llm.py)"],
  ["유사 검색은 문자 n-gram(임베딩 아님)", "이력이 쌓이면 사내 임베딩 또는 API 임베딩 검토"],
  ["HWP(구형식)는 읽기만 지원(자동 변환), 회신 출력은 HWPX. 암호·배포용 문서는 변환 불가", "부서 회신 서식 HWPX 1종 템플릿화, 필요 시 한글에서 일반 HWPX로 재저장 안내"],
  ["접속 인증은 부서 공유 비밀번호 1개", "Cloud Run 앞에 IAP(Google 계정 로그인) 또는 사내 SSO 연동"],
  ["국감 요구자료·내부 수치가 Google Cloud(서울 리전)에 저장됨", "사내 보안 규정상 허용 여부 확인 [확인 필요]. 허용되지 않으면 같은 코드를 사내 공용 PC·사내 서버에서 run_server.bat으로 운영"],
  ["충족 검사(코드)는 지표명 언급 수준", "AI 추가 점검과 병행, 판정 규칙 보강"],
  ["다른 LLM API 미지원(현재 Anthropic)", "OpenAI 호환 공급자 1개 추가로 GPT·Gemini·사내 게이트웨이 연결(반나절 작업량), 모델별 추출 품질 재채점 필요"],
]));
children.push(H3("4-5. 오픈소스·라이브러리와 출처"));
children.push(P("설치된 패키지 메타데이터 기준 라이선스. 모두 상용·사내 사용이 허용되는 허용적 라이선스이며, 수정·재배포 없이 라이브러리로만 사용한다."));
children.push(grid([2600, 1400, 2200, 3438], ["라이브러리", "버전", "라이선스", "용도"], [
  ["streamlit", "1.65.0", "Apache-2.0", "웹 화면"], ["pandas", "3.0.6", "BSD-3-Clause", "표 처리·대조"], ["anthropic", "1.11.0", "MIT", "Claude API SDK"],
  ["openpyxl", "3.1.5", "MIT", "엑셀 읽기·쓰기"], ["pypdf", "6.19.0", "BSD-3-Clause", "PDF 본문 추출"], ["python-docx", "1.2.0", "MIT", "DOCX 읽기"],
  ["python-hwpx", "6.7.0", "Apache-2.0", "HWP 5.0 → HWPX 변환"], ["playwright", "1.63.0", "Apache-2.0", "화면 검증·시연 녹화(개발용)"],
  ["pytest / ruff", "9.1.1 / 0.16.10", "MIT", "테스트·린트(개발용)"], ["Litestream", "0.3.13", "Apache-2.0 [확인 필요]", "SQLite → Cloud Storage 복제(운영)"],
  ["Pretendard 글꼴", "CDN", "SIL OFL 1.1 [확인 필요]", "화면 글꼴(차단 시 맑은 고딕)"], ["Material Symbols", "Google Fonts", "Apache-2.0 [확인 필요]", "화면 아이콘"],
], { center: [1, 2] }));
children.push(P("외부 자료 인용: 법률저널(공노총 설문), 이투데이·이데일리(국감자료 수치 오류 사례), 국정원 「생성형 AI 활용 보안 가이드라인」(2023), 행정안전부 디자인 시스템 KRDS. 상세 URL은 소스 ZIP의 docs/조사_배경자료_2026-10-04.md에 있으며, 수치 인용은 보도 요약문 기준이라 원문 확인이 필요하다.", { before: 120 }));

// 붙임
children.push(H2("붙임. 제출물 구성과 확인 방법"));
children.push(grid([2400, 7238], ["제출물", "내용"], [
  ["① 프로젝트 보고서", "본 문서(PDF)"],
  ["② 작동 시연 영상", "시연영상_EBS_요구자료대응에이전트.mp4 (3분 이내, 1440×900). 가상 데이터로 홈 → 과거 답변 등록 → 1·2·3단계 → 검토·승인 → 이력·현황·이력에 묻기 → 설정 순"],
  ["③ 프로토타입 결과물", `웹 접속 URL: ${URL} (비밀번호는 메일 본문) · 소스 코드 ZIP(app/ 전체, 샘플 데이터·서식·문서 포함) · GitHub 저장소 lts134/ebsjajucenter`],
  ["확인 방법(심사위원)", "URL 접속 → 비밀번호 → 설정 → 시연·초기화의 '시연 데이터 넣기' → 홈부터 영상 순서대로. AI 경로를 보려면 설정 → AI 연결에 Claude API 키 입력(키는 세션에만 보관). 키가 없으면 규칙 기반으로 같은 흐름이 동작"],
  ["소스 ZIP 안의 문서", "README.md(설치·실행), docs/운영_매뉴얼.md(실데이터 전환·장애 대응), docs/시연_시나리오.md, docs/배포_클라우드.md, docs/실측_기록_2026-10-04.md, docs/제출_설명서.md, storage/llm_check_*.md(추출 채점표)"],
], { firstLabel: true }));
children.push(P("", { after: 200 }));
children.push(P([run("2026년 10월    일", { size: 20 })], { align: AlignmentType.CENTER, before: 300 }));
children.push(P([run("제출자  지역교육협력부  이택수  (서명)", { size: 20 })], { align: AlignmentType.CENTER }));

// ---------- 문서 ----------
const doc = new Document({
  creator: "EBS 지역교육협력부", title: "[서식5] 프로젝트 개발 보고서 — EBS 대외 요구자료 대응 에이전트",
  styles: { default: { document: { run: { font: FONT, size: 20, color: TEXT } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 26, bold: true, color: TEXT }, paragraph: { outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 24, bold: true, color: BLUE }, paragraph: { outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 21, bold: true, color: TEXT }, paragraph: { outlineLevel: 2 } },
    ] },
  numbering: { config: [{ reference: "bul", levels: [
    { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 360, hanging: 240 } } } },
    { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 240 } } } } ] }] },
  sections: [{
    properties: { page: { size: { width: PAGE_W, height: 16838 }, margin: { top: 1300, bottom: 1200, left: MARGIN, right: MARGIN } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [run("2026 EBS AI Innovation Challenge · [서식5] 프로젝트 개발 보고서 · EBS 대외 요구자료 대응 에이전트", { size: 15, color: MUTED })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "- ", font: FONT, size: 16 }), new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16 }), new TextRun({ text: " -", font: FONT, size: 16 })] })] }) },
    children,
  }],
});
Packer.toBuffer(doc).then(buf => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT, (buf.length / 1024).toFixed(0), "KB, font", FONT); });
