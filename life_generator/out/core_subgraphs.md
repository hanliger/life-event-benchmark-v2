# Core Subgraphs

각 core_subgraph는 한 사람의 life path에 삽입될 수 있는 핵심 event-order motif다.
시간 구간은 이전 event 이후 몇 년 뒤에 다음 event가 발생하는지를 뜻한다.

## 결혼

- id: `marriage_only_core`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `24-70`
- sampling weight: `4.0`
- note: 출산이나 입양 없이 결혼/재혼만 발생하는 경우를 표현한다.

```text
결혼 [self] actions=FA-05,FA-08,FA-09
```

## 결혼-출산-자녀독립

- id: `marriage_childbirth_education_arc`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `24-40`
- sampling weight: `8.0`
- note: 출산 이후 자녀 교육 milestone은 자녀 나이를 기준으로 지연 구간을 둔다.

```text
결혼 [self] actions=FA-05,FA-08,FA-09
  -> 출산 [child] child_age=0 actions=FA-01,FA-08,FA-09 (+1-5y)
  -> 초등학교 입학 [child] child_age=6-7 actions=FA-08,FA-09 (+6-7y)
  -> 중학교 입학 [child] child_age=12-13 actions=FA-08,FA-09 (+5-6y)
  -> 고등학교 입학 [child] child_age=15-16 actions=FA-08,FA-09 (+3y)
  -> 자녀 독립 [child] child_age=18-30 actions=FA-05,FA-08,FA-09 (+2-14y)
```

## 결혼-입양-자녀독립

- id: `marriage_adoption_education_arc`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `27-45`
- sampling weight: `1.5`
- note: 입양 시 자녀 나이에 따라 이미 지난 학교 milestone은 생략한다.

```text
결혼 [self] actions=FA-05,FA-08,FA-09
  -> 입양 [child] child_age=0-17 actions=FA-01,FA-08,FA-09 (+1-6y)
  -> 초등학교 입학 [child] child_age=6-7 actions=FA-08,FA-09 (+1-7y)
  -> 중학교 입학 [child] child_age=12-13 actions=FA-08,FA-09 (+5-6y)
  -> 고등학교 입학 [child] child_age=15-16 actions=FA-08,FA-09 (+3y)
  -> 자녀 독립 [child] child_age=18-30 actions=FA-05,FA-08,FA-09 (+2-14y)
```

## 별거-이혼

- id: `separation_divorce_core`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `32-58`
- sampling weight: `2.0`
- note: 분가와 재혼은 항상 이혼 뒤에만 발생한다고 보지 않으므로 별도 core sampling으로 연결한다.

```text
별거 [self] actions=FA-01,FA-07,FA-08
  -> 이혼 [self] actions=FA-01,FA-07,FA-08 (+1-3y)
```

## 분가

- id: `separate_household_core`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `18-70`
- sampling weight: `3.5`
- note: 분가는 별거/이혼 이전, 도중, 이후 모두 가능하므로 단일 core로 둔다.

```text
분가 [household] actions=FA-05,FA-08,FA-09
```

## 부양가족 발생

- id: `dependent_care_core`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `35-68`
- sampling weight: `4.0`
- note: 요양 종료는 사망/회복/돌봄 종료와 연결될 수 있으므로 별도 terminal core로 둔다.

```text
부양가족 발생 [parent] actions=FA-01,FA-07,FA-08
```

## 부모 요양 종료

- id: `parent_care_end_core`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `35-85`
- sampling weight: `3.0`
- note: 가족 사망 core 직후 또는 장기 돌봄 종료 뒤에 core sampling으로 연결한다.

```text
부모 요양 종료 [parent] actions=FA-01,FA-08
```

## 가족 사망

- id: `family_bereavement_core`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `25-80`
- sampling weight: `2.0`

```text
가족 사망 [family] actions=FA-01,FA-07,FA-08
```

## 독립-이사-월세계약

- id: `self_independence_residence_core`
- domain: `relationship`
- kind: `core_subgraph`
- start age: `19-35`
- sampling weight: `6.0`
- note: 개인 독립은 주거 형성으로 이어지는 핵심 전이로 본다.

```text
독립 [self] actions=FA-05,FA-08,FA-09
  -> 이사 [household] actions=FA-01,FA-08 (+0-1y)
  -> 월세계약 [household] actions=FA-07,FA-08,FA-09 (+0-1y)
```

## 취업-교육-이직

- id: `early_career_learning_arc`
- domain: `career`
- kind: `core_subgraph`
- start age: `22-32`
- sampling weight: `7.0`

```text
취업 [self] actions=FA-03,FA-08,FA-09
  -> 교육 [self] actions=FA-04,FA-07,FA-09 (+1-5y)
  -> 이직 [self] actions=FA-03,FA-04,FA-08 (+1-6y)
  -> 전근 [self] actions=FA-01,FA-08 (+1-5y)
```

## 휴직-복직-재교육

- id: `leave_return_reskilling_arc`
- domain: `career`
- kind: `core_subgraph`
- start age: `28-48`
- sampling weight: `3.0`
- note: 고용 상태가 선행되어야 붙을 수 있는 career core다.

```text
휴직 [self] actions=FA-01,FA-08,FA-09
  -> 복직 [self] actions=FA-03,FA-08 (+1-3y)
  -> 교육 [self] actions=FA-04,FA-07,FA-09 (+0-3y)
```

## 회사 지원 유학-복귀

- id: `employer_sponsored_study_obligation_arc`
- domain: `career`
- kind: `core_subgraph`
- start age: `25-42`
- sampling weight: `1.5`

```text
취업 [self] actions=FA-03,FA-08,FA-09
  -> 유학 [self] actions=FA-04,FA-07,FA-09 (+3-10y)
  -> 복직 [self] actions=FA-03,FA-08 (+2-5y)
  -> 교육 [self] actions=FA-04,FA-07,FA-09 (+0-2y)
  -> 이직 [self] actions=FA-03,FA-04,FA-08 (+3-8y)
```

## 퇴사-창업-재취업

- id: `employment_to_startup_reentry_arc`
- domain: `career`
- kind: `core_subgraph`
- start age: `32-55`
- sampling weight: `2.0`
- note: 기존 고용 상태가 있어야 시작될 수 있는 career transition core다.

```text
퇴사 [self] actions=FA-01,FA-04,FA-08,FA-09
  -> 창업/프리랜서 전환 [self] actions=FA-05,FA-08,FA-10 (+0-2y)
  -> 폐업/사업 중단 [self] actions=FA-01,FA-08,FA-10 (+2-10y)
  -> 실직 [self] actions=FA-01,FA-04,FA-08,FA-09 (+0-1y)
  -> 취업 [self] actions=FA-03,FA-08,FA-09 (+0-4y)
```

## 은퇴-연금

- id: `retirement_pension_arc`
- domain: `career`
- kind: `core_subgraph`
- start age: `50-65`
- sampling weight: `5.0`

```text
은퇴 준비 시작 [self] actions=FA-02,FA-04,FA-09
  -> 연금수령시작 [self] actions=FA-02,FA-03,FA-08 (+2-8y)
```

## 임차 계약 변경

- id: `rental_contract_long_cycle`
- domain: `residence`
- kind: `core_subgraph`
- start age: `22-45`
- sampling weight: `5.0`

```text
이사 [household] actions=FA-01,FA-08
  -> 전세계약 [household] actions=FA-04,FA-07,FA-09 (+0-1y)
  -> 주거 계약 변경 [household] actions=FA-04,FA-07,FA-08 (+2-4y)
  -> 퇴거 [household] actions=FA-01,FA-08 (+2-4y)
  -> 이사 [household] actions=FA-01,FA-08 (+0-1y)
  -> 월세계약 [household] actions=FA-07,FA-08,FA-09 (+0-1y)
  -> 주거 계약 변경 [household] actions=FA-04,FA-07,FA-08 (+2-4y)
```

## 임차-자가-매각

- id: `rental_to_homeownership_lifecycle`
- domain: `residence`
- kind: `core_subgraph`
- start age: `27-50`
- sampling weight: `4.0`

```text
전세계약 [household] actions=FA-04,FA-07,FA-09
  -> 주택 구매 [household] actions=FA-04,FA-07,FA-10 (+3-12y)
  -> 이사 [household] actions=FA-01,FA-08 (+0-1y)
  -> 주택 매각 [household] actions=FA-01,FA-08,FA-10 (+8-25y)
  -> 이사 [household] actions=FA-01,FA-08 (+0-1y)
```

## 가족 질병

- id: `accident_family_illness_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `25-70`
- sampling weight: `1.2`

```text
가족 질병 [family] actions=FA-01,FA-07,FA-09
```

## 가족 입원

- id: `accident_family_hospitalization_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `25-70`
- sampling weight: `1.1`

```text
가족 입원 [family] actions=FA-01,FA-07,FA-09
```

## 가족 수술

- id: `accident_family_surgery_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `25-70`
- sampling weight: `0.9`

```text
가족 수술 [family] actions=FA-01,FA-07,FA-09
```

## 부양 가족 발생

- id: `accident_dependent_added_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `25-75`
- sampling weight: `1.0`

```text
부양 가족 발생 [family] actions=FA-01,FA-07,FA-08
```

## 부모 요양 종료

- id: `accident_parent_care_end_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `35-80`
- sampling weight: `0.8`

```text
부모 요양 종료 [parent] actions=FA-01,FA-08
```

## 가족 사망

- id: `accident_family_death_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `25-80`
- sampling weight: `0.8`

```text
가족 사망 [family] actions=FA-01,FA-07,FA-08
```

## 사고

- id: `accident_accident_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `20-75`
- sampling weight: `1.0`

```text
사고 [self] actions=FA-01,FA-07,FA-10
```

## 재난발생

- id: `accident_disaster_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `20-75`
- sampling weight: `0.6`

```text
재난발생 [self] actions=FA-01,FA-07,FA-10
```

## 금융사기

- id: `accident_financial_fraud_core`
- domain: `accident`
- kind: `core_subgraph`
- start age: `25-70`
- sampling weight: `0.8`

```text
금융사기 발생 [self] actions=FA-01,FA-06
```
