# Stage 2.2 Prompt Leakage Audit

## 결론

현재 `stage2_2_reconstruct-v3` prompt에는 Gold state, `dynamic_paths`, Gold evidence가 직접 전달되지 않으며 checkpoint 이후 session도 포함되지 않는다. 다만 34개 path의 자료형과 closed candidate values는 공개된다. 특히 candidate가 하나뿐인 path는 값 추론 난이도를 낮출 수 있다. 이 prompt는 이번 비교에서 수정하지 않고 limitation으로 보고한다.

이 문서는 생성 prompt를 바꾸는 specification이 아니라 현재 protocol의 disclosure 및 leakage audit 기록이다. 실행 시 `audit-prompt`가 method별 runtime 검사를 수행하고 `traj_001`의 checkpoint 15와 300 rendering을 `prompts/audit_examples/*.txt.gz`로 보존한다.

## System prompt 전문

```text
당신은 금융 상담 이력에서 과거 Life Event와 그 시점의 금융 상태를 판정한다.
계획·희망·검토·신청과 실제 발생을 구분한다.
취소·철회·정정·갱신이 있으면 질문에 지정된 기간을 기준으로 판단한다.
현재 질의 checkpoint 이후의 정보는 사용하지 않는다.
설명 없이 <answer>정답</answer> 형식으로만 답한다.
```

마지막 `<answer>` 지시는 Stage 2.2 user prompt의 “JSON 객체 하나만 출력” 지시와 충돌할 수 있다. Parser/schema failure가 발생할 수 있으므로 first attempt를 보존하고 해당 failure에만 최대 1회 retry한다.

## User prompt 전문

`prompts.py::_build_stage2_2_query`가 아래 고정 본문과 동적 schema/evidence를 결합한다.

```text
초기 금융 memory와 checkpoint까지의 상담만 사용하여 현재 상태를 복원하세요.
미래 계획이나 가능성은 현재 사실로 반영하지 마세요.
일반 조회와 과거 회상은 명시적인 현재 상태 변경이 아니면 초기/최신 상태를 유지하세요.
각 path의 value와 status는 checkpoint 시점의 최종 상태여야 합니다.
초기 상태와 달라진 path에는 이를 뒷받침하는 D### 상담 ID를 하나 이상 쓰세요.
초기 상태와 같은 path의 evidence_session_ids는 빈 배열로 쓰세요.
설명, Markdown, 코드 펜스 없이 JSON 객체 하나만 출력하세요.

[허용 status]
current, historical, stale, needs_verification, unknown, not_applicable

[필수 state schema: 아래 34개 path를 정확히 한 번씩 모두 출력]
<34개 path별 value schema>

[출력 구조 예시 — 값은 예시가 아니라 placeholder]
{"schema_version":"stage2_2_reconstruct-v3","state":{"<각 required path>":{"value":"<현재 값 또는 null>","status":"<허용 status>","evidence_session_ids":["D015"]}}}

[초기 상태와 상담 이력]
<S000 및 method가 선택한 D001…Dcheckpoint>

[질문]
<frozen Stage 2.2 reconstruction question>
```

34개 required path는 다음과 같다.

```text
profile.age
profile.locale
profile.region
household.marital_status
household.spouse_or_partner
household.children
household.dependents
household.child_support_arrangement
employment.employment_status
employment.employer
employment.occupation
employment.income_stability
employment.salary_day
employment.salary_account
housing.residence_status
housing.address
housing.contract_type
housing.rent_amount
housing.rent_payee
housing.maintenance_fee_payee
housing.mortgage_status
housing.properties
housing.primary_residence_property_id
education.self_education_status
education.child_education_stage
financial_products.checking_accounts
financial_products.savings_accounts
financial_products.loans
financial_products.pension_or_irp
goals.emergency_fund
goals.housing_deposit_goal
goals.child_education_goal
goals.retirement_goal
cashflow.recent_one_off_expense
```

## Candidate values 노출

Prompt builder는 closed value 후보를 schema에 포함한다. 주요 노출 후보는 다음과 같다.

- `household.marital_status`: `single`, `married`, `separated`, `divorced`, `widowed`
- `household.spouse_or_partner`: `spouse`, `partner`, `null`
- `employment.employment_status`: `employed`, `self_employed`, `unemployed`, `on_leave`, `retired`, `student`, `homemaker`
- `employment.income_stability`: `stable`, `variable`, `reduced`, `unstable`, `retired`, `null`
- `employment.salary_day`: `10`, `15`, `21`, `25`, `null`
- `employment.salary_account`: `main_checking`, `null`
- `housing.residence_status` 및 `housing.contract_type`: `owner`, `jeonse`, `wolse`, `public_rental`, `company_housing`, `dormitory`, `family_home`, `other`, 필요 시 `null`
- `housing.mortgage_status`: `none`, `active`, `closed`
- `education.self_education_status`: `none`, `enrolled`, `study_abroad`, `on_leave`, `completed`
- `education.child_education_stage`: `preschool`, `primary`, `middle`, `high`, `adult`, `null`
- `financial_products.checking_accounts`: `main_checking`
- `financial_products.savings_accounts`: `savings_1`
- `financial_products.loans`: `mortgage`, `jeonse_loan`, `credit`, `auto_loan`, `student_loan`, `business_loan`
- `financial_products.pension_or_irp`: `irp`, `receiving`, `null`
- 각 goal path: 해당 path의 `active` 또는 `building`, `null`
- object path의 role/status/category 후보도 schema에 노출된다.

`checking_accounts`, `savings_accounts`처럼 단일 non-null candidate만 갖는 path는 dialogue에서 “존재 여부”만 파악하면 값을 사실상 자동 완성할 수 있다. 이는 candidate value prediction이 아니라 state tracking 능력을 측정한다는 task 정의와 일부 정합하지만, 절대 난이도와 model 간 분별력을 낮출 수 있으므로 single-candidate path를 별도 sensitivity analysis로 보고한다.

## Method별 실제 prompt 구성

| Method | S000 | Dialogue/evidence | Retrieval query |
|---|---|---|---|
| Full Context 5종 | prompt 첫 evidence | D001부터 checkpoint까지 전부 | 없음 |
| BM25 | 검색 예산 밖에서 prompt 첫 evidence | 4개 query의 top-5를 deduplicate한 최대 20 session | 아래 frozen query |
| Dense | 검색 예산 밖에서 prompt 첫 evidence | BM25와 같은 budget, cosine top-5/group | 아래 frozen query |
| Mem0 | ingest 후 prompt 첫 evidence | memory search 결과에서 source session을 보존한 최대 20 evidence | 아래 frozen query |
| Letta | archival ingest와 prompt 첫 evidence 모두에 포함 | agent가 최대 4회 archival search로 선택 | 아래 query를 agent protocol에 전달 |

선택 evidence는 모두 원래 session 순서로 재정렬한다. 이전 checkpoint prediction이나 response는 다음 checkpoint prompt에 포함하지 않는다.

## Frozen Gold-independent retrieval queries

모든 query는 path name, 자료형, 중립적인 한국어 의미 설명만 사용한다. candidate values, Gold state, Gold evidence, `dynamic_paths`는 포함하지 않는다.

1. `profile_household`: profile 및 household path의 현재 사실, 가족·배우자·자녀·부양가족의 실제 발생·취소·정정·갱신을 검색한다.
2. `employment_education`: employment 및 education path의 고용·직장·소득 안정성·급여·교육 관련 최신 사실을 검색한다.
3. `housing_financial_products`: housing 및 financial_products path의 주거·주소·임대차·부동산·계좌·대출·연금 관련 최신 사실을 검색한다.
4. `goals_cashflow`: goals 및 cashflow path의 목표와 최근 일회성 지출 관련 최신 사실을 검색한다.

각 runtime query의 정확한 typed-path 전문은 다음 명령으로 확인한다.

```bash
./experiment/scripts/paid/run_stage2_2.sh show-prompt \
  --method bm25_claude_opus_4_8 \
  --trajectory traj_001 \
  --checkpoint 15
```

## Code-level leakage 근거

- Prompt builder는 `build_query(item, evidence)`에 공개 schema와 선택 evidence만 전달한다. `item["gold"]`는 scorer에서만 읽는다.
- Retrieval query builder는 `VALUE_KINDS`와 고정된 neutral descriptions만 import하며 `SCALAR_CLOSED_VALUES`, Gold, `dynamic_paths`를 참조하지 않는다.
- Evaluator는 각 checkpoint에 `sessions[:checkpoint]`만 ingest하거나 그 시점의 immutable snapshot을 clone한다.
- Runtime audit는 prompt에 나타난 모든 `[D### |` marker가 checkpoint 이하인지 검사한다.
- `state_pairs`의 Gold와 metrics는 generation 완료 후 별도 artifact로 결합한다.

## Runtime audit와 limitation

```bash
./experiment/scripts/paid/run_stage2_2.sh audit-prompt --run-dir <run-dir>
```

`prompt_audit.json`이 통과하기 전에는 paid `execute`와 `resume`이 차단된다. Audit 후에도 prompt를 자동 수정하지 않는다. 따라서 candidate exposure와 system/user output-format conflict는 이 run의 명시적 limitation으로 남는다.
