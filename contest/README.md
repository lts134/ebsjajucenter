# 2026 활용수기 공모 온라인 접수

학생 접수 화면과 관리자 화면입니다. 화면은 Firebase Hosting에 올리고, 접수 정보와 PDF는 Firebase(서울 리전)에 저장합니다. 학생은 아임웹 `jajucenter.ebs.co.kr/event` 페이지 안에서 접수 화면을 봅니다.

```
학생 ── jajucenter.ebs.co.kr/event (아임웹) ── 안에 끼워 넣은 화면 ── jajucenter-4ce99.web.app/apply ──▶ Firebase(서울)
관리자 ── jajucenter-4ce99.web.app/admin (홈페이지 메뉴에 노출하지 않음) ──────────────────────────────▶ Firebase(서울)
```

## 파일 구성

| 파일 | 용도 |
|---|---|
| `public/apply.html` | 학생 접수 화면 → `https://jajucenter-4ce99.web.app/apply` |
| `public/admin.html` | 관리자 화면 → `https://jajucenter-4ce99.web.app/admin` |
| `firebase.json` | Hosting 주소 정리, 보안 헤더, 규칙 파일 위치 |
| `firestore.rules` | 접수 정보 보안 규칙 |
| `storage.rules` | PDF 파일 보안 규칙 |
| `cors.json` | 관리자 화면에서 PDF를 받기 위한 설정 |
| `imweb-embed.html` | 아임웹 `/event` 페이지의 코드 요소에 붙여넣을 코드 |

## 처음 설정 순서

### 1. Firebase 프로젝트 (콘솔에서)

1. 공식 Google 계정으로 [Firebase 콘솔](https://console.firebase.google.com)에서 프로젝트 `jajucenter-4ce99`를 만듭니다. (완료)
2. 요금제를 Blaze(종량제)로 바꿉니다. Google Cloud 콘솔 > 결제 > 예산 및 알림에서 월 10,000원 예산 알림을 설정합니다. 알림은 메일만 보내고 사용을 멈추지는 않습니다.
3. Firestore Database > 데이터베이스 만들기: 위치 `asia-northeast3 (서울)`, 이름은 기본값 `(default)`.
4. Storage > 시작하기: 위치 `asia-northeast3 (서울)`.
5. Authentication > 시작하기 > 로그인 방법에서 **Google**을 사용 설정합니다. `jajucenter-4ce99.web.app`은 승인된 도메인에 기본으로 들어 있습니다.
6. 프로젝트 설정 > 내 앱 > 웹 앱 추가. 표시되는 `firebaseConfig` 값을 `public/apply.html`과 `public/admin.html` 상단 `FIREBASE_CONFIG`에 똑같이 붙여넣습니다.

### 2. 화면과 보안 규칙 올리기 (내 PC에서, 처음 1회)

1. [Node.js](https://nodejs.org) LTS 버전을 설치합니다.
2. 이 `contest` 폴더를 PC에 내려받습니다.
3. 명령 프롬프트(맥은 터미널)를 열고 아래를 순서대로 실행합니다.
   ```
   npm install -g firebase-tools
   firebase login
   cd 내려받은경로/contest
   firebase deploy
   ```
   - `firebase login`: 브라우저가 열리면 공식 계정으로 로그인합니다.
   - 올릴 프로젝트(`jajucenter-4ce99`)는 `.firebaserc`에 지정되어 있습니다.
   - `firebase deploy`: 화면 2개와 보안 규칙 2개가 함께 올라갑니다. Storage 규칙이 Firestore를 읽는 권한을 부여할지 물으면 `Y`를 입력합니다.
4. 끝나면 `https://jajucenter-4ce99.web.app/apply`가 열리는지 확인합니다.

이후 화면이나 문구를 고쳤을 때는 같은 폴더에서 `firebase deploy --only hosting`만 실행하면 됩니다.

### 3. PDF 내려받기 설정 (1회)

1. Google Cloud 콘솔 오른쪽 위 Cloud Shell을 열고 `cors.json`을 업로드한 뒤 실행합니다. 버킷 이름은 `FIREBASE_CONFIG`의 `storageBucket` 값입니다.
   ```
   gcloud storage buckets update gs://버킷이름 --cors-file=cors.json
   ```

### 4. 관리자 등록과 응모 기간

1. `https://jajucenter-4ce99.web.app/admin`을 열어 공식 계정으로 로그인합니다. "관리자 권한이 없습니다" 화면에 나오는 UID를 복사합니다.
2. Firebase 콘솔 > Firestore > 데이터에서 컬렉션 `config`, 문서 ID `admins`를 만들고 필드 `uids`(배열)에 UID를 넣습니다. 관리자를 추가할 때도 이 배열에 UID를 더합니다.
3. 관리자 화면을 새로고침하고 [응모 기간 설정]에서 시작·마감 시각(한국시각)을 저장합니다. 기간이 없으면 접수를 받지 않습니다. 나중에 언제든 바꿀 수 있습니다.

### 5. 아임웹 `/event` 페이지

1. 아임웹 사이트 관리에서 새 페이지를 만들고 주소를 `event`로 정합니다.
2. 페이지에 **코드** 요소를 추가하고 `imweb-embed.html` 내용을 전부 붙여넣습니다.
3. 아임웹 환경설정 > 보안 및 개인정보 보호 > iframe 사용 시 허용되는 도메인에 `jajucenter-4ce99.web.app`을 등록합니다. [메뉴 이름은 아임웹 화면에서 확인 필요]
4. 게시한 뒤 휴대폰과 PC에서 `jajucenter.ebs.co.kr/event`를 열어 접수 화면이 보이는지 확인합니다.
5. 위탁사에 팝업 연결 주소 `https://jajucenter.ebs.co.kr/event`를 전달합니다.

**참고**
- 접수 화면은 `jajucenter.ebs.co.kr`, `자기주도학습센터.kr`(`xn--ok0bv9ht5mcnbrxlpkcd3xm0h.kr`), `*.imweb.me` 안에서만 보이도록 막아 두었습니다(`firebase.json`의 `frame-ancestors`). 아임웹 편집기 미리보기에서 화면이 안 보이면, 게시된 실제 주소에서 확인합니다. 홈페이지 주소가 바뀌면 이 값을 고친 뒤 `firebase deploy --only hosting`을 실행합니다.
- 관리자 화면은 다른 사이트 안에 끼워 넣을 수 없도록 막아 두었습니다. 아임웹에 넣지 말고 `web.app` 주소로 직접 엽니다.
- 아임웹 코드 요소가 스크립트를 실행하지 않는 경우에도 접수는 됩니다. 이때 화면 높이가 2600px로 고정되어 아래쪽에 여백이 생길 수 있습니다.

## 운영 전 확인할 문구 [확인 필요]

`public/apply.html`에서 아래 항목을 확정한 값으로 바꾸고 `firebase deploy --only hosting`을 실행합니다.

- `GRADE_OPTIONS`: 응모 대상 학년
- `CENTER_OPTIONS`: 센터 목록 (비워 두면 학생이 직접 입력)
- `CONTACT_TEXT`: 문의처
- 개인정보 수집·이용 안내 표의 **보유 기간**
- 처리 위탁 문구의 수탁자 법인명 (Google LLC 여부)
- 홈페이지 개인정보 처리방침에 위탁 사항(수탁자: Google, 업무: 공모 접수 정보·파일 저장) 추가

## 공개 전 시험 (실제 프로젝트에서)

| 시험 | 기대 결과 |
|---|---|
| 로그인하지 않은 휴대폰으로 `jajucenter.ebs.co.kr/event`에서 PDF 제출 | 접수번호 표시 |
| 한글(.hwp) 파일 또는 10MB 초과 파일 제출 | 제출 거부 안내 |
| 응모 기간 밖에서 제출 | 제출 거부 |
| 관리자로 등록되지 않은 다른 Google 계정으로 `/admin` 로그인 | "관리자 권한이 없습니다" |
| 관리자 계정으로 PDF [보기] | PDF가 새 탭에 열림 (안 열리면 3번 CORS 확인) |

시험 제출 건은 관리자 화면에서 "제외"로 바꾸거나, 공개 직전에 [전체 파기]로 지웁니다.

## 운영 중 유의사항

- **블라인드 심사**: [심사용 ZIP]은 파일명을 심사번호로 바꿉니다. 동의서 쪽 수를 입력하면 PDF 뒤쪽에서 그만큼 빼고 묶습니다. 학생이 순서를 바꿔 넣었을 수 있으니 몇 건은 직접 열어 확인합니다.
- **파일 전달**: 심사위원에게는 링크가 아니라 내려받은 파일로 전달합니다.
- **중복 제출**: 성명·학교·보호자 연락처가 같으면 마지막 제출본에 "최종본", 이전 것에 "이전본"이 표시됩니다. 이전본은 "제외"로 처리합니다.
- **파기**: 보유기간이 끝나면 관리자 화면 [전체 파기]로 삭제하고, 파기 일시와 건수를 기록합니다.
- **외부 파일**: 화면은 `www.gstatic.com`(Firebase), `cdnjs.cloudflare.com`(엑셀·ZIP·PDF 처리), Google Fonts를 불러옵니다.

## 보안 규칙 요약

- 학생(로그인 없음): 응모 기간 안에 **새 접수만** 가능합니다. 조회·수정·삭제는 할 수 없습니다. PDF·10MB 이하·무작위 파일명만 허용하고, 이미 있는 파일은 덮어쓸 수 없습니다.
- 관리자(`config/admins`에 등록된 UID): 조회, 다운로드, 처리 상태·메모·심사번호 수정, 삭제가 가능합니다. 학생이 낸 신청 내용은 관리자도 고칠 수 없습니다.
- 응모 기간은 서버 시각으로 판단하므로 학생 기기 시계를 바꿔도 마감 후에는 제출할 수 없습니다.
