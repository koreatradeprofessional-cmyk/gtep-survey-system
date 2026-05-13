# GTEP 활동 평가 시스템 - Render + Supabase 배포용

이 버전은 학생 응답 데이터를 로컬 SQLite가 아니라 **Supabase PostgreSQL**에 저장하도록 만든 배포용 버전입니다.
Render 무료 웹서비스와 Supabase 무료 DB 조합으로 외부 접속 URL을 학생들에게 배포할 수 있습니다.

## 주요 기능

- 통합 로그인 화면
  - 학생: 아이디 = 학번, 비밀번호 = 이름
  - 관리자: 아이디 = `admin`, 비밀번호 = `5750!`
- 로그인 화면에 관리자 모드 문구를 별도로 표시하지 않음
- 학생 화면에서 학번/이름 노출 최소화
- 자기평가 제외
- 학생별 평가 대상 자동 생성
- 직무팀 평가, 직무팀 팀원 평가, 박람회팀 평가, 박람회팀 팀원 평가, 다른 박람회팀 5개 평가
- 관리자 대시보드
  - 제출률
  - 많이 언급된 단어
  - 전체 학생 순위
  - 박람회팀 순위
  - 직무팀 순위
  - 응답 품질 지표
  - 원문 응답 조회
  - CSV 다운로드
- Supabase PostgreSQL 저장
- Render 배포용 `render.yaml` 포함

---

## 1. 로컬 테스트

Supabase 없이 로컬에서 테스트하면 자동으로 SQLite 파일을 사용합니다.

```powershell
cd gtep_survey_system
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

PowerShell에서 가상환경 실행이 막히면, 아래처럼 가상환경 없이 테스트할 수 있습니다.

```powershell
cd gtep_survey_system
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

접속 주소:

```text
http://127.0.0.1:8000
```

---

## 2. Supabase 준비

1. Supabase에서 새 프로젝트를 만듭니다.
2. Project Settings > Database > Connection string > URI를 복사합니다.
3. URI의 `[YOUR-PASSWORD]` 부분을 실제 DB 비밀번호로 바꿉니다.
4. URI 끝에 `?sslmode=require`가 없다면 추가합니다.

예시:

```text
postgresql://postgres.xxxxx:비밀번호@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require
```

앱은 Render에서 처음 실행될 때 Supabase DB에 필요한 테이블을 자동으로 생성하고, `seed_data.csv`의 학생 명단을 자동 등록합니다.

수동으로 테이블을 만들고 싶다면 `supabase_schema.sql` 내용을 Supabase SQL Editor에서 실행하면 됩니다.

---

## 3. Render 배포 방법

### 방법 A: GitHub에 올려서 배포

1. 이 폴더 전체를 GitHub 저장소에 업로드합니다.
2. Render에서 New > Web Service를 선택합니다.
3. GitHub 저장소를 연결합니다.
4. 아래 설정을 입력합니다.

| 항목 | 값 |
|---|---|
| Environment | Python |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app:app --host 0.0.0.0 --port $PORT` |
| Plan | Free |

5. Environment Variables에 아래 값을 추가합니다.

| Key | Value |
|---|---|
| `DATABASE_URL` | Supabase PostgreSQL URI |
| `GTEP_SECRET_KEY` | 임의의 긴 문자열 |
| `GTEP_ADMIN_ID` | `admin` |
| `GTEP_ADMIN_PASSWORD` | `5750!` |

6. Deploy를 누릅니다.

배포가 끝나면 Render가 아래와 같은 주소를 제공합니다.

```text
https://gtep-survey-system.onrender.com
```

이 주소를 학생들에게 배포하면 됩니다.

### 방법 B: Blueprint 배포

`render.yaml`이 포함되어 있으므로 Render Blueprint로도 배포할 수 있습니다.
단, `DATABASE_URL`은 민감정보이므로 Render 화면에서 직접 입력해야 합니다.

---

## 4. 로그인 정보

### 학생

```text
아이디: 학번
비밀번호: 이름
```

### 관리자

```text
아이디: admin
비밀번호: 5750!
```

로그인 화면에는 관리자 모드가 따로 표시되지 않습니다. `admin / 5750!`을 입력하면 자동으로 운영 대시보드로 이동합니다.

---

## 5. 운영 전 확인사항

배포 후 실제 학생에게 공지하기 전에 반드시 아래를 확인하세요.

1. 관리자 로그인 가능 여부
2. 학생 1명으로 로그인 가능 여부
3. 임시저장 가능 여부
4. 최종 제출 가능 여부
5. 관리자 대시보드 제출률 갱신 여부
6. 원문 응답 조회 가능 여부
7. CSV 다운로드 가능 여부

---

## 6. 데이터 백업

Supabase에 응답이 저장되지만, 설문 종료 후에는 관리자 대시보드에서 CSV를 다운로드해 별도로 보관하는 것을 권장합니다.

---

## 7. 보안 메모

- Render 환경변수에서 `GTEP_SECRET_KEY`는 반드시 기본값이 아닌 긴 무작위 문자열로 설정하세요.
- 관리자 비밀번호 `5750!`은 요청에 따라 기본값으로 넣어두었지만, 실제 운영 중 변경하려면 Render 환경변수 `GTEP_ADMIN_PASSWORD` 값을 바꾸면 됩니다.
- 학생 비밀번호는 현재 이름 기반입니다. 보안성을 높이려면 학생별 임시 비밀번호 컬럼을 추가하는 방식으로 확장할 수 있습니다.
