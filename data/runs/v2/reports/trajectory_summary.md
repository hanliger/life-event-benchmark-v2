# Trajectory Life Event Summary

- Trajectories: 20
- Target occurred life events per trajectory: 20

## 1. traj_001

- Persona: p_5e8d9df03584 (26세, 직업상태=unemployed, 혼인=single, 주거=owner, 자녀=0명)
- Horizon: 231개월 (26세 → 45세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 26세 +2개월 | 2 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"미래정보시스템","new_salary_day":10}` | `traj_001_ev001` |
| 2 | 27세 +1개월 | 13 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"family_care"}` | `traj_001_ev002` |
| 3 | 27세 +5개월 | 17 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":15,"previous_employer":"previous_employer"}` | `traj_001_ev003` |
| 4 | 28세 +3개월 | 27 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_001_ev005` |
| 5 | 28세 +7개월 | 31 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"서연식품","new_salary_day":25}` | `traj_001_ev004` |
| 6 | 33세 +5개월 | 89 | 자영업 전환 | `career_self_employment` | `{"self_employment_type":"startup"}` | `traj_001_ev007` |
| 7 | 33세 +5개월 | 89 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_001_ev008` |
| 8 | 34세 +7개월 | 103 | 이사 | `housing_move` | `{"move_reason":"return_to_family_home","new_address":"경기 수원시 영통구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":400000,"new_residence_status":"wolse"}` | `traj_001_ev009` |
| 9 | 35세 +7개월 | 115 | 결혼 | `relationship_marriage` | `{"joint_living_expense_amount":500000,"partner_ref":"spouse"}` | `traj_001_ev010` |
| 10 | 36세 +1개월 | 121 | 출산 | `relationship_childbirth` | `{"children_after":[0],"dependents_after":1,"family_change_type":"birth"}` | `traj_001_ev011` |
| 11 | 36세 +9개월 | 129 | 입양 | `relationship_adoption` | `{"children_after":[0],"dependents_after":1,"family_change_type":"adoption"}` | `traj_001_ev012` |
| 12 | 37세 +2개월 | 134 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_001_ev013` |
| 13 | 38세 +4개월 | 148 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"서울 송파구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_001_ev014` |
| 14 | 38세 +4개월 | 148 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_001_ev015` |
| 15 | 38세 +7개월 | 151 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":500000}` | `traj_001_ev016` |
| 16 | 40세 +0개월 | 168 | 입양 | `relationship_adoption` | `{"children_after":[0,3],"dependents_after":1,"family_change_type":"adoption"}` | `traj_001_ev017` |
| 17 | 41세 +2개월 | 182 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_001_ev018` |
| 18 | 42세 +5개월 | 197 | 출산 | `relationship_childbirth` | `{"children_after":[0,1,4],"dependents_after":2,"family_change_type":"birth"}` | `traj_001_ev019` |
| 19 | 44세 +9개월 | 225 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"primary"}` | `traj_001_ev020` |
| 20 | 45세 +3개월 | 231 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"sibling","dependents_after":1,"was_dependent":true}` | `traj_001_ev021` |

## 2. traj_002

- Persona: p_e59d3090a517 (20세, 직업상태=employed, 혼인=single, 주거=jeonse, 자녀=0명)
- Horizon: 209개월 (20세 → 37세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 20세 +3개월 | 3 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"서울 송파구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_002_ev001` |
| 2 | 20세 +6개월 | 6 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_002_ev002` |
| 3 | 21세 +1개월 | 13 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_002_ev003` |
| 4 | 21세 +7개월 | 19 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"서울 관악구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":800000,"new_residence_status":"wolse"}` | `traj_002_ev004` |
| 5 | 21세 +10개월 | 22 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"신영유통","new_salary_day":25}` | `traj_002_ev005` |
| 6 | 24세 +1개월 | 49 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"서연식품","new_salary_day":25}` | `traj_002_ev007` |
| 7 | 24세 +10개월 | 58 | 이사 | `housing_move` | `{"move_reason":"return_to_family_home","new_address":"서울 송파구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":650000,"new_residence_status":"wolse"}` | `traj_002_ev008` |
| 8 | 25세 +0개월 | 60 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":1500000}` | `traj_002_ev009` |
| 9 | 27세 +6개월 | 90 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_002_ev011` |
| 10 | 30세 +4개월 | 124 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":4000000}` | `traj_002_ev013` |
| 11 | 31세 +2개월 | 134 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":1200000,"mortgage_payment_day":27,"new_address":"부산 해운대구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":true,"post_purchase_residence_status":"owner"}` | `traj_002_ev015` |
| 12 | 32세 +1개월 | 145 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":3000000}` | `traj_002_ev017` |
| 13 | 32세 +2개월 | 146 | 이사 | `housing_move` | `{"move_reason":"return_to_family_home","new_address":"서울 관악구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_002_ev016` |
| 14 | 32세 +8개월 | 152 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"두레헬스케어","new_salary_day":10}` | `traj_002_ev018` |
| 15 | 32세 +9개월 | 153 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"온새미로디자인","new_salary_day":25}` | `traj_002_ev019` |
| 16 | 32세 +10개월 | 154 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_002_ev020` |
| 17 | 34세 +10개월 | 178 | 자영업 전환 | `career_self_employment` | `{"self_employment_type":"freelance"}` | `traj_002_ev021` |
| 18 | 35세 +0개월 | 180 | 결혼 | `relationship_marriage` | `{"joint_living_expense_amount":300000,"partner_ref":"spouse"}` | `traj_002_ev022` |
| 19 | 36세 +10개월 | 202 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"other_adult","dependents_after":1,"support_amount":500000}` | `traj_002_ev023` |
| 20 | 37세 +5개월 | 209 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":null,"marital_status_after":"separated","relationship_transition_type":"separation"}` | `traj_002_ev024` |

## 3. traj_003

- Persona: p_ba20d81ebde7 (26세, 직업상태=unemployed, 혼인=single, 주거=family_home, 자녀=0명)
- Horizon: 111개월 (26세 → 35세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 26세 +1개월 | 1 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"두레헬스케어","new_salary_day":10}` | `traj_003_ev001` |
| 2 | 26세 +4개월 | 4 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"서울 관악구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_003_ev002` |
| 3 | 26세 +7개월 | 7 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"health"}` | `traj_003_ev003` |
| 4 | 27세 +5개월 | 17 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_003_ev004` |
| 5 | 27세 +9개월 | 21 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":21,"previous_employer":"previous_employer"}` | `traj_003_ev005` |
| 6 | 28세 +6개월 | 30 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":2000000,"mortgage_payment_day":27,"new_address":"부산 해운대구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":false,"post_purchase_residence_status":"owner"}` | `traj_003_ev006` |
| 7 | 28세 +8개월 | 32 | 결혼 | `relationship_marriage` | `{"joint_living_expense_amount":1000000,"partner_ref":"spouse"}` | `traj_003_ev007` |
| 8 | 29세 +2개월 | 38 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"부산 해운대구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_003_ev008` |
| 9 | 29세 +4개월 | 40 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"family_care"}` | `traj_003_ev010` |
| 10 | 30세 +5개월 | 53 | 입양 | `relationship_adoption` | `{"children_after":[0],"dependents_after":1,"family_change_type":"adoption"}` | `traj_003_ev012` |
| 11 | 30세 +8개월 | 56 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"광주 서구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_003_ev013` |
| 12 | 31세 +3개월 | 63 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_003_ev015` |
| 13 | 31세 +10개월 | 70 | 출산 | `relationship_childbirth` | `{"children_after":[0,0],"dependents_after":2,"family_change_type":"birth"}` | `traj_003_ev014` |
| 14 | 31세 +10개월 | 70 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"서울 송파구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_003_ev016` |
| 15 | 32세 +6개월 | 78 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":25,"previous_employer":"previous_employer"}` | `traj_003_ev017` |
| 16 | 32세 +6개월 | 78 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"두레헬스케어","new_salary_day":21}` | `traj_003_ev018` |
| 17 | 34세 +6개월 | 102 | 입양 | `relationship_adoption` | `{"children_after":[0,2,2],"dependents_after":3,"family_change_type":"adoption"}` | `traj_003_ev020` |
| 18 | 34세 +8개월 | 104 | 출산 | `relationship_childbirth` | `{"children_after":[0,3,3],"dependents_after":3,"family_change_type":"birth"}` | `traj_003_ev021` |
| 19 | 34세 +9개월 | 105 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_003_ev022` |
| 20 | 35세 +3개월 | 111 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"신영유통","new_salary_day":10}` | `traj_003_ev023` |

## 4. traj_004

- Persona: p_90d9542da2c4 (23세, 직업상태=unemployed, 혼인=single, 주거=wolse, 자녀=0명)
- Horizon: 138개월 (23세 → 34세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 23세 +6개월 | 6 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"경기 성남시 분당구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":400000,"new_residence_status":"wolse"}` | `traj_004_ev001` |
| 2 | 23세 +9개월 | 9 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":500000}` | `traj_004_ev002` |
| 3 | 24세 +4개월 | 16 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_004_ev003` |
| 4 | 25세 +0개월 | 24 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"경기 성남시 분당구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":400000,"new_residence_status":"wolse"}` | `traj_004_ev004` |
| 5 | 25세 +1개월 | 25 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"미래정보시스템","new_salary_day":21}` | `traj_004_ev005` |
| 6 | 26세 +4개월 | 40 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"대성엔지니어링","new_salary_day":15}` | `traj_004_ev006` |
| 7 | 26세 +9개월 | 45 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"sibling","dependents_after":0,"was_dependent":false}` | `traj_004_ev008` |
| 8 | 27세 +4개월 | 52 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"health"}` | `traj_004_ev009` |
| 9 | 28세 +7개월 | 67 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":900000,"mortgage_payment_day":10,"new_address":"광주 서구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":true,"post_purchase_residence_status":"owner"}` | `traj_004_ev010` |
| 10 | 28세 +8개월 | 68 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":15,"previous_employer":"previous_employer"}` | `traj_004_ev011` |
| 11 | 29세 +7개월 | 79 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"두레헬스케어","new_salary_day":10}` | `traj_004_ev013` |
| 12 | 29세 +8개월 | 80 | 결혼 | `relationship_marriage` | `{"joint_living_expense_amount":700000,"partner_ref":"spouse"}` | `traj_004_ev012` |
| 13 | 29세 +11개월 | 83 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_004_ev014` |
| 14 | 31세 +3개월 | 99 | 입양 | `relationship_adoption` | `{"children_after":[0],"dependents_after":1,"family_change_type":"adoption"}` | `traj_004_ev015` |
| 15 | 31세 +11개월 | 107 | 출산 | `relationship_childbirth` | `{"children_after":[0,0],"dependents_after":2,"family_change_type":"birth"}` | `traj_004_ev017` |
| 16 | 32세 +2개월 | 110 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":2000000}` | `traj_004_ev018` |
| 17 | 32세 +10개월 | 118 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"한빛물류","new_salary_day":25}` | `traj_004_ev019` |
| 18 | 33세 +3개월 | 123 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_004_ev020` |
| 19 | 34세 +5개월 | 137 | 주택 매각 | `housing_home_sale` | `{"ownership_transition":"dispose","post_sale_contract_type":"other","post_sale_residence_status":"other"}` | `traj_004_ev023` |
| 20 | 34세 +6개월 | 138 | 입양 | `relationship_adoption` | `{"children_after":[0,2,2],"dependents_after":3,"family_change_type":"adoption"}` | `traj_004_ev022` |

## 5. traj_005

- Persona: p_a13bf7e107ce (36세, 직업상태=unemployed, 혼인=married, 주거=wolse, 자녀=1명)
- Horizon: 168개월 (36세 → 50세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 36세 +1개월 | 1 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"미래정보시스템","new_salary_day":25}` | `traj_005_ev001` |
| 2 | 36세 +6개월 | 6 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":3000000}` | `traj_005_ev002` |
| 3 | 36세 +11개월 | 11 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"두레헬스케어","new_salary_day":15}` | `traj_005_ev003` |
| 4 | 38세 +2개월 | 26 | 입양 | `relationship_adoption` | `{"children_after":[0,11],"dependents_after":2,"family_change_type":"adoption"}` | `traj_005_ev005` |
| 5 | 38세 +4개월 | 28 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_005_ev006` |
| 6 | 38세 +5개월 | 29 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":300000,"marital_status_after":"divorced","relationship_transition_type":"divorce"}` | `traj_005_ev007` |
| 7 | 39세 +0개월 | 36 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":200000,"new_stage":"middle"}` | `traj_005_ev009` |
| 8 | 39세 +1개월 | 37 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"경기 성남시 분당구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_005_ev008` |
| 9 | 40세 +1개월 | 49 | 결혼 | `relationship_marriage` | `{"joint_living_expense_amount":700000,"partner_ref":"spouse"}` | `traj_005_ev010` |
| 10 | 40세 +11개월 | 59 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":2000000,"mortgage_payment_day":27,"new_address":"경기 성남시 분당구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":true,"post_purchase_residence_status":"owner"}` | `traj_005_ev012` |
| 11 | 41세 +1개월 | 61 | 이사 | `housing_move` | `{"move_reason":"caregiving","new_address":"대구 수성구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":650000,"new_residence_status":"wolse"}` | `traj_005_ev013` |
| 12 | 41세 +4개월 | 64 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"서연식품","new_salary_day":15}` | `traj_005_ev014` |
| 13 | 42세 +3개월 | 75 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"high"}` | `traj_005_ev015` |
| 14 | 45세 +1개월 | 109 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"청람교육","new_salary_day":21}` | `traj_005_ev016` |
| 15 | 45세 +1개월 | 109 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":200000,"new_stage":"primary"}` | `traj_005_ev017` |
| 16 | 45세 +8개월 | 116 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"other_adult","dependents_after":3,"support_amount":300000}` | `traj_005_ev018` |
| 17 | 46세 +1개월 | 121 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"family_care"}` | `traj_005_ev019` |
| 18 | 47세 +3개월 | 135 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":25,"previous_employer":"previous_employer"}` | `traj_005_ev020` |
| 19 | 49세 +5개월 | 161 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":1000000}` | `traj_005_ev023` |
| 20 | 50세 +0개월 | 168 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"광주 서구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_005_ev024` |

## 6. traj_006

- Persona: p_57eb527f5138 (39세, 직업상태=employed, 혼인=married, 주거=wolse, 자녀=2명)
- Horizon: 131개월 (39세 → 49세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 39세 +5개월 | 5 | 이사 | `housing_move` | `{"move_reason":"return_to_family_home","new_address":"서울 관악구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_006_ev001` |
| 2 | 39세 +7개월 | 7 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"미래정보시스템","new_salary_day":15}` | `traj_006_ev002` |
| 3 | 41세 +2개월 | 26 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"middle"}` | `traj_006_ev004` |
| 4 | 41세 +11개월 | 35 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_006_ev006` |
| 5 | 42세 +1개월 | 37 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":2000000,"mortgage_payment_day":10,"new_address":"대전 유성구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":false,"post_purchase_residence_status":"owner"}` | `traj_006_ev007` |
| 6 | 43세 +5개월 | 53 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":0,"end_reason":"child_independence"}` | `traj_006_ev008` |
| 7 | 43세 +11개월 | 59 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"신영유통","new_salary_day":25}` | `traj_006_ev009` |
| 8 | 44세 +1개월 | 61 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":200000,"new_stage":"high"}` | `traj_006_ev010` |
| 9 | 44세 +4개월 | 64 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"health"}` | `traj_006_ev011` |
| 10 | 45세 +1개월 | 73 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":21,"previous_employer":"previous_employer"}` | `traj_006_ev012` |
| 11 | 45세 +8개월 | 80 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":2000000}` | `traj_006_ev014` |
| 12 | 45세 +10개월 | 82 | 출산 | `relationship_childbirth` | `{"children_after":[0,17,25],"dependents_after":1,"family_change_type":"birth"}` | `traj_006_ev013` |
| 13 | 46세 +6개월 | 90 | 이사 | `housing_move` | `{"move_reason":"caregiving","new_address":"대구 수성구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_006_ev015` |
| 14 | 46세 +8개월 | 92 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":0,"end_reason":"child_independence"}` | `traj_006_ev016` |
| 15 | 47세 +9개월 | 105 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"대전 유성구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_006_ev018` |
| 16 | 47세 +11개월 | 107 | 입양 | `relationship_adoption` | `{"children_after":[0,1,18,26],"dependents_after":1,"family_change_type":"adoption"}` | `traj_006_ev017` |
| 17 | 48세 +1개월 | 109 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"other","dependents_after":1,"was_dependent":false}` | `traj_006_ev019` |
| 18 | 49세 +2개월 | 122 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"other_adult","dependents_after":2,"support_amount":300000}` | `traj_006_ev020` |
| 19 | 49세 +6개월 | 126 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_006_ev021` |
| 20 | 49세 +11개월 | 131 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_006_ev022` |

## 7. traj_007

- Persona: p_9af25c927991 (38세, 직업상태=employed, 혼인=married, 주거=owner, 자녀=2명)
- Horizon: 144개월 (38세 → 50세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 38세 +2개월 | 2 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"high"}` | `traj_007_ev002` |
| 2 | 38세 +9개월 | 9 | 입양 | `relationship_adoption` | `{"children_after":[0,6,16],"dependents_after":3,"family_change_type":"adoption"}` | `traj_007_ev001` |
| 3 | 39세 +2개월 | 14 | 이사 | `housing_move` | `{"move_reason":"caregiving","new_address":"부산 해운대구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_007_ev004` |
| 4 | 39세 +8개월 | 20 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"두레헬스케어","new_salary_day":15}` | `traj_007_ev005` |
| 5 | 40세 +5개월 | 29 | 이사 | `housing_move` | `{"move_reason":"caregiving","new_address":"서울 관악구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_007_ev008` |
| 6 | 40세 +6개월 | 30 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"primary"}` | `traj_007_ev007` |
| 7 | 40세 +11개월 | 35 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_007_ev009` |
| 8 | 42세 +0개월 | 48 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":2,"end_reason":"parent_care_end"}` | `traj_007_ev011` |
| 9 | 42세 +6개월 | 54 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"한빛물류","new_salary_day":21}` | `traj_007_ev012` |
| 10 | 43세 +0개월 | 60 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_007_ev013` |
| 11 | 44세 +0개월 | 72 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"primary"}` | `traj_007_ev015` |
| 12 | 44세 +9개월 | 81 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":4000000}` | `traj_007_ev016` |
| 13 | 45세 +5개월 | 89 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":1500000}` | `traj_007_ev017` |
| 14 | 46세 +0개월 | 96 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_007_ev018` |
| 15 | 46세 +2개월 | 98 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"middle"}` | `traj_007_ev019` |
| 16 | 46세 +11개월 | 107 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":1,"end_reason":"parent_care_end"}` | `traj_007_ev022` |
| 17 | 47세 +1개월 | 109 | 입양 | `relationship_adoption` | `{"children_after":[0,8,14,24],"dependents_after":3,"family_change_type":"adoption"}` | `traj_007_ev020` |
| 18 | 47세 +11개월 | 119 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"서연식품","new_salary_day":21}` | `traj_007_ev023` |
| 19 | 48세 +2개월 | 122 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":1000000,"new_stage":"high"}` | `traj_007_ev024` |
| 20 | 50세 +0개월 | 144 | 이사 | `housing_move` | `{"move_reason":"other","new_address":"서울 송파구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":400000,"new_residence_status":"wolse"}` | `traj_007_ev025` |

## 8. traj_008

- Persona: p_e12bc82b3ad5 (39세, 직업상태=employed, 혼인=married, 주거=jeonse, 자녀=0명)
- Horizon: 186개월 (39세 → 54세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 39세 +6개월 | 6 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"parent","dependents_after":1,"support_amount":300000}` | `traj_008_ev001` |
| 2 | 40세 +0개월 | 12 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"서울 송파구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_008_ev003` |
| 3 | 40세 +2개월 | 14 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"두레헬스케어","new_salary_day":21}` | `traj_008_ev004` |
| 4 | 41세 +2개월 | 26 | 이사 | `housing_move` | `{"move_reason":"caregiving","new_address":"서울 송파구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_008_ev005` |
| 5 | 42세 +8개월 | 44 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"서연식품","new_salary_day":10}` | `traj_008_ev006` |
| 6 | 43세 +11개월 | 59 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"health"}` | `traj_008_ev008` |
| 7 | 44세 +0개월 | 60 | 이사 | `housing_move` | `{"move_reason":"return_to_family_home","new_address":"경기 수원시 영통구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":400000,"new_residence_status":"wolse"}` | `traj_008_ev007` |
| 8 | 45세 +9개월 | 81 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":25,"previous_employer":"previous_employer"}` | `traj_008_ev011` |
| 9 | 46세 +5개월 | 89 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"두레헬스케어","new_salary_day":15}` | `traj_008_ev012` |
| 10 | 47세 +1개월 | 97 | 이사 | `housing_move` | `{"move_reason":"other","new_address":"서울 마포구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_008_ev013` |
| 11 | 47세 +7개월 | 103 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_008_ev014` |
| 12 | 48세 +8개월 | 116 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_008_ev015` |
| 13 | 48세 +11개월 | 119 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"온새미로디자인","new_salary_day":21}` | `traj_008_ev016` |
| 14 | 49세 +8개월 | 128 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"부산 해운대구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":500000,"new_residence_status":"wolse"}` | `traj_008_ev017` |
| 15 | 51세 +0개월 | 144 | 은퇴 | `retirement_start` | `{"pension_started_same_time":false,"previous_employment_status":"employed","retirement_reason":"health"}` | `traj_008_ev018` |
| 16 | 51세 +0개월 | 144 | 이사 | `housing_move` | `{"move_reason":"caregiving","new_address":"인천 연수구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":800000,"new_residence_status":"wolse"}` | `traj_008_ev019` |
| 17 | 51세 +0개월 | 144 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"parent","dependents_after":2,"support_amount":200000}` | `traj_008_ev020` |
| 18 | 52세 +10개월 | 166 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"부산 해운대구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_008_ev021` |
| 19 | 53세 +4개월 | 172 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":1,"end_reason":"parent_care_end"}` | `traj_008_ev022` |
| 20 | 54세 +6개월 | 186 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_008_ev024` |

## 9. traj_009

- Persona: p_279ec7a44378 (34세, 직업상태=employed, 혼인=married, 주거=wolse, 자녀=0명)
- Horizon: 111개월 (34세 → 43세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 34세 +4개월 | 4 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"경기 수원시 영통구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_009_ev001` |
| 2 | 34세 +5개월 | 5 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"child","dependents_after":0,"was_dependent":false}` | `traj_009_ev002` |
| 3 | 35세 +2개월 | 14 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_009_ev003` |
| 4 | 35세 +7개월 | 19 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_009_ev004` |
| 5 | 35세 +11개월 | 23 | 출산 | `relationship_childbirth` | `{"children_after":[0],"dependents_after":1,"family_change_type":"birth"}` | `traj_009_ev005` |
| 6 | 36세 +4개월 | 28 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"한빛물류","new_salary_day":10}` | `traj_009_ev006` |
| 7 | 37세 +11개월 | 47 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"온새미로디자인","new_salary_day":15}` | `traj_009_ev007` |
| 8 | 37세 +11개월 | 47 | 이사 | `housing_move` | `{"move_reason":"other","new_address":"서울 송파구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_009_ev008` |
| 9 | 38세 +7개월 | 55 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_009_ev009` |
| 10 | 38세 +9개월 | 57 | 출산 | `relationship_childbirth` | `{"children_after":[0,3],"dependents_after":2,"family_change_type":"birth"}` | `traj_009_ev010` |
| 11 | 38세 +10개월 | 58 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_009_ev011` |
| 12 | 39세 +11개월 | 71 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"청람교육","new_salary_day":21}` | `traj_009_ev012` |
| 13 | 40세 +7개월 | 79 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"서울 마포구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_009_ev013` |
| 14 | 41세 +0개월 | 84 | 입양 | `relationship_adoption` | `{"children_after":[0,2,5],"dependents_after":1,"family_change_type":"adoption"}` | `traj_009_ev014` |
| 15 | 41세 +0개월 | 84 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_009_ev015` |
| 16 | 41세 +3개월 | 87 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"미래정보시스템","new_salary_day":15}` | `traj_009_ev016` |
| 17 | 42세 +0개월 | 96 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"서울 송파구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_009_ev018` |
| 18 | 42세 +5개월 | 101 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_009_ev019` |
| 19 | 42세 +11개월 | 107 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"primary"}` | `traj_009_ev020` |
| 20 | 43세 +3개월 | 111 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":900000,"mortgage_payment_day":27,"new_address":"광주 서구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":true,"post_purchase_residence_status":"owner"}` | `traj_009_ev021` |

## 10. traj_010

- Persona: p_702dd652ff75 (30세, 직업상태=employed, 혼인=single, 주거=jeonse, 자녀=0명)
- Horizon: 124개월 (30세 → 40세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 30세 +3개월 | 3 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"광주 서구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":1000000,"new_residence_status":"wolse"}` | `traj_010_ev001` |
| 2 | 31세 +3개월 | 15 | 결혼 | `relationship_marriage` | `{"joint_living_expense_amount":1000000,"partner_ref":"spouse"}` | `traj_010_ev002` |
| 3 | 31세 +8개월 | 20 | 입양 | `relationship_adoption` | `{"children_after":[0],"dependents_after":1,"family_change_type":"adoption"}` | `traj_010_ev004` |
| 4 | 31세 +11개월 | 23 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"other"}` | `traj_010_ev005` |
| 5 | 32세 +1개월 | 25 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"대구 수성구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":800000,"new_residence_status":"wolse"}` | `traj_010_ev006` |
| 6 | 32세 +5개월 | 29 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":10,"previous_employer":"previous_employer"}` | `traj_010_ev007` |
| 7 | 32세 +8개월 | 32 | 출산 | `relationship_childbirth` | `{"children_after":[0,1],"dependents_after":2,"family_change_type":"birth"}` | `traj_010_ev008` |
| 8 | 32세 +8개월 | 32 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_010_ev009` |
| 9 | 33세 +4개월 | 40 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"parent","dependents_after":3,"support_amount":300000}` | `traj_010_ev010` |
| 10 | 33세 +11개월 | 47 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"spouse","dependents_after":3,"was_dependent":false}` | `traj_010_ev011` |
| 11 | 35세 +6개월 | 66 | 출산 | `relationship_childbirth` | `{"children_after":[0,2,3],"dependents_after":4,"family_change_type":"birth"}` | `traj_010_ev012` |
| 12 | 35세 +6개월 | 66 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_010_ev014` |
| 13 | 36세 +3개월 | 75 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"서울 송파구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":1000000,"new_residence_status":"wolse"}` | `traj_010_ev015` |
| 14 | 36세 +7개월 | 79 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":200000,"marital_status_after":"separated","relationship_transition_type":"separation"}` | `traj_010_ev016` |
| 15 | 37세 +11개월 | 95 | 이사 | `housing_move` | `{"move_reason":"other","new_address":"광주 서구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_010_ev019` |
| 16 | 38세 +5개월 | 101 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"primary"}` | `traj_010_ev020` |
| 17 | 39세 +2개월 | 110 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"대구 수성구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_010_ev021` |
| 18 | 39세 +9개월 | 117 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"신영유통","new_salary_day":21}` | `traj_010_ev022` |
| 19 | 40세 +3개월 | 123 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_010_ev023` |
| 20 | 40세 +4개월 | 124 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":3,"end_reason":"parent_care_end"}` | `traj_010_ev024` |

## 11. traj_011

- Persona: p_f47b28561c3a (41세, 직업상태=employed, 혼인=married, 주거=jeonse, 자녀=1명)
- Horizon: 119개월 (41세 → 50세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 41세 +1개월 | 1 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"primary"}` | `traj_011_ev002` |
| 2 | 41세 +2개월 | 2 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_011_ev001` |
| 3 | 41세 +4개월 | 4 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"other_adult","dependents_after":2,"support_amount":300000}` | `traj_011_ev003` |
| 4 | 41세 +10개월 | 10 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":900000,"mortgage_payment_day":15,"new_address":"부산 해운대구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":false,"post_purchase_residence_status":"owner"}` | `traj_011_ev004` |
| 5 | 41세 +10개월 | 10 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"미래정보시스템","new_salary_day":10}` | `traj_011_ev005` |
| 6 | 42세 +4개월 | 16 | 자영업 전환 | `career_self_employment` | `{"self_employment_type":"freelance"}` | `traj_011_ev007` |
| 7 | 42세 +11개월 | 23 | 출산 | `relationship_childbirth` | `{"children_after":[0,7],"dependents_after":3,"family_change_type":"birth"}` | `traj_011_ev006` |
| 8 | 43세 +2개월 | 26 | 입양 | `relationship_adoption` | `{"children_after":[0,8],"dependents_after":3,"family_change_type":"adoption"}` | `traj_011_ev008` |
| 9 | 43세 +4개월 | 28 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":2,"end_reason":"parent_care_end"}` | `traj_011_ev009` |
| 10 | 45세 +11개월 | 59 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"parent","dependents_after":3,"support_amount":500000}` | `traj_011_ev010` |
| 11 | 46세 +3개월 | 63 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":2,"end_reason":"parent_care_end"}` | `traj_011_ev011` |
| 12 | 47세 +0개월 | 72 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":500000,"marital_status_after":"separated","relationship_transition_type":"separation"}` | `traj_011_ev013` |
| 13 | 47세 +1개월 | 73 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"middle"}` | `traj_011_ev014` |
| 14 | 48세 +6개월 | 90 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":1,"end_reason":"parent_care_end"}` | `traj_011_ev015` |
| 15 | 48세 +9개월 | 93 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_011_ev016` |
| 16 | 49세 +0개월 | 96 | 고용 종료 | `career_employment_end` | `{"end_reason":"business_closure"}` | `traj_011_ev017` |
| 17 | 49세 +5개월 | 101 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"미래정보시스템","new_salary_day":21}` | `traj_011_ev018` |
| 18 | 50세 +2개월 | 110 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":1000000,"new_stage":"high"}` | `traj_011_ev019` |
| 19 | 50세 +9개월 | 117 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_011_ev020` |
| 20 | 50세 +11개월 | 119 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"온새미로디자인","new_salary_day":10}` | `traj_011_ev021` |

## 12. traj_012

- Persona: p_7ed134ba985e (42세, 직업상태=employed, 혼인=married, 주거=owner, 자녀=1명)
- Horizon: 134개월 (42세 → 53세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 42세 +3개월 | 3 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"high"}` | `traj_012_ev002` |
| 2 | 42세 +10개월 | 10 | 출산 | `relationship_childbirth` | `{"children_after":[0,16],"dependents_after":2,"family_change_type":"birth"}` | `traj_012_ev001` |
| 3 | 43세 +11개월 | 23 | 유학·장기연수 | `education_study_abroad` | `{}` | `traj_012_ev004` |
| 4 | 44세 +2개월 | 26 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":300000,"marital_status_after":"divorced","relationship_transition_type":"divorce"}` | `traj_012_ev005` |
| 5 | 45세 +3개월 | 39 | 주택 매각 | `housing_home_sale` | `{"ownership_transition":"dispose","post_sale_contract_type":"other","post_sale_residence_status":"other"}` | `traj_012_ev006` |
| 6 | 46세 +4개월 | 52 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"한빛물류","new_salary_day":25}` | `traj_012_ev007` |
| 7 | 46세 +8개월 | 56 | 이사 | `housing_move` | `{"move_reason":"other","new_address":"경기 성남시 분당구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":1000000,"new_residence_status":"wolse"}` | `traj_012_ev008` |
| 8 | 47세 +2개월 | 62 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":1500000,"mortgage_payment_day":10,"new_address":"인천 연수구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":false,"post_purchase_residence_status":"owner"}` | `traj_012_ev009` |
| 9 | 47세 +10개월 | 70 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_012_ev010` |
| 10 | 48세 +0개월 | 72 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"서연식품","new_salary_day":21}` | `traj_012_ev011` |
| 11 | 48세 +2개월 | 74 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"primary"}` | `traj_012_ev012` |
| 12 | 48세 +2개월 | 74 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"other_adult","dependents_after":3,"support_amount":500000}` | `traj_012_ev013` |
| 13 | 48세 +5개월 | 77 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_012_ev014` |
| 14 | 49세 +8개월 | 92 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_012_ev016` |
| 15 | 50세 +0개월 | 96 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"신영유통","new_salary_day":25}` | `traj_012_ev018` |
| 16 | 50세 +5개월 | 101 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"primary"}` | `traj_012_ev019` |
| 17 | 50세 +10개월 | 106 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"parent","dependents_after":2,"was_dependent":true}` | `traj_012_ev020` |
| 18 | 51세 +6개월 | 114 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":1000000}` | `traj_012_ev021` |
| 19 | 52세 +0개월 | 120 | 주택 매각 | `housing_home_sale` | `{"ownership_transition":"dispose","post_sale_contract_type":"family_home","post_sale_residence_status":"family_home"}` | `traj_012_ev022` |
| 20 | 53세 +2개월 | 134 | 은퇴 | `retirement_start` | `{"pension_started_same_time":true,"previous_employment_status":"employed","retirement_reason":"family"}` | `traj_012_ev023` |

## 13. traj_013

- Persona: p_9c4f26bb90bb (47세, 직업상태=employed, 혼인=single, 주거=jeonse, 자녀=0명)
- Horizon: 137개월 (47세 → 58세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 47세 +0개월 | 0 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"spouse","dependents_after":0,"was_dependent":false}` | `traj_013_ev001` |
| 2 | 47세 +2개월 | 2 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":4000000}` | `traj_013_ev002` |
| 3 | 48세 +1개월 | 13 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"대전 유성구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":800000,"new_residence_status":"wolse"}` | `traj_013_ev004` |
| 4 | 48세 +6개월 | 18 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":3000000}` | `traj_013_ev005` |
| 5 | 50세 +1개월 | 37 | 이직 | `career_job_change` | `{"change_type":"external_employer","new_employer":"청람교육","new_salary_day":21}` | `traj_013_ev006` |
| 6 | 50세 +6개월 | 42 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"child","dependents_after":0,"was_dependent":false}` | `traj_013_ev008` |
| 7 | 50세 +8개월 | 44 | 결혼 | `relationship_marriage` | `{"joint_living_expense_amount":1000000,"partner_ref":"spouse"}` | `traj_013_ev007` |
| 8 | 51세 +1개월 | 49 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"parent","dependents_after":1,"support_amount":300000}` | `traj_013_ev009` |
| 9 | 51세 +5개월 | 53 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_013_ev010` |
| 10 | 52세 +5개월 | 65 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"대구 수성구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_013_ev011` |
| 11 | 52세 +10개월 | 70 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_013_ev012` |
| 12 | 53세 +6개월 | 78 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"parent","dependents_after":1,"support_amount":200000}` | `traj_013_ev013` |
| 13 | 54세 +2개월 | 86 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_013_ev014` |
| 14 | 54세 +4개월 | 88 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"두레헬스케어","new_salary_day":10}` | `traj_013_ev015` |
| 15 | 54세 +7개월 | 91 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"광주 서구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_013_ev016` |
| 16 | 55세 +8개월 | 104 | 연금 수령 시작 | `retirement_pension_start` | `{}` | `traj_013_ev017` |
| 17 | 56세 +4개월 | 112 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":900000,"mortgage_payment_day":15,"new_address":"부산 해운대구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":true,"post_purchase_residence_status":"owner"}` | `traj_013_ev018` |
| 18 | 56세 +6개월 | 114 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"other_adult","dependents_after":1,"support_amount":300000}` | `traj_013_ev020` |
| 19 | 56세 +7개월 | 115 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":null,"marital_status_after":"divorced","relationship_transition_type":"divorce"}` | `traj_013_ev019` |
| 20 | 58세 +5개월 | 137 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_013_ev021` |

## 14. traj_014

- Persona: p_f09b95a852ad (42세, 직업상태=employed, 혼인=married, 주거=jeonse, 자녀=1명)
- Horizon: 132개월 (42세 → 53세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 42세 +6개월 | 6 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"경기 성남시 분당구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_014_ev001` |
| 2 | 42세 +7개월 | 7 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_014_ev002` |
| 3 | 43세 +6개월 | 18 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":1000000,"new_stage":"middle"}` | `traj_014_ev003` |
| 4 | 44세 +2개월 | 26 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"other"}` | `traj_014_ev004` |
| 5 | 44세 +3개월 | 27 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"parent","dependents_after":2,"support_amount":500000}` | `traj_014_ev005` |
| 6 | 45세 +2개월 | 38 | 입양 | `relationship_adoption` | `{"children_after":[0,13],"dependents_after":3,"family_change_type":"adoption"}` | `traj_014_ev006` |
| 7 | 45세 +11개월 | 47 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":1000000,"new_stage":"middle"}` | `traj_014_ev008` |
| 8 | 46세 +3개월 | 51 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":21,"previous_employer":"previous_employer"}` | `traj_014_ev009` |
| 9 | 47세 +4개월 | 64 | 이사 | `housing_move` | `{"move_reason":"return_to_family_home","new_address":"광주 서구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":800000,"new_residence_status":"wolse"}` | `traj_014_ev010` |
| 10 | 48세 +3개월 | 75 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":200000,"new_stage":"high"}` | `traj_014_ev011` |
| 11 | 48세 +8개월 | 80 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_014_ev012` |
| 12 | 48세 +8개월 | 80 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"부산 해운대구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_014_ev013` |
| 13 | 49세 +4개월 | 88 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"청람교육","new_salary_day":25}` | `traj_014_ev014` |
| 14 | 49세 +11개월 | 95 | 이사 | `housing_move` | `{"move_reason":"other","new_address":"대구 수성구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_014_ev015` |
| 15 | 50세 +1개월 | 97 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_014_ev016` |
| 16 | 50세 +6개월 | 102 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_014_ev017` |
| 17 | 51세 +7개월 | 115 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"primary"}` | `traj_014_ev018` |
| 18 | 52세 +4개월 | 124 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"경기 수원시 영통구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":650000,"new_residence_status":"wolse"}` | `traj_014_ev019` |
| 19 | 52세 +5개월 | 125 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":2,"end_reason":"parent_care_end"}` | `traj_014_ev020` |
| 20 | 53세 +0개월 | 132 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":300000,"marital_status_after":"divorced","relationship_transition_type":"divorce"}` | `traj_014_ev021` |

## 15. traj_015

- Persona: p_b8c20bc99c0d (40세, 직업상태=employed, 혼인=married, 주거=owner, 자녀=1명)
- Horizon: 135개월 (40세 → 51세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 40세 +2개월 | 2 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"primary"}` | `traj_015_ev002` |
| 2 | 40세 +3개월 | 3 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"대전 유성구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_015_ev003` |
| 3 | 40세 +5개월 | 5 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_015_ev001` |
| 4 | 40세 +10개월 | 10 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"서연식품","new_salary_day":15}` | `traj_015_ev004` |
| 5 | 40세 +10개월 | 10 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":4000000}` | `traj_015_ev005` |
| 6 | 41세 +1개월 | 13 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_015_ev006` |
| 7 | 42세 +5개월 | 29 | 자영업 전환 | `career_self_employment` | `{"self_employment_type":"freelance"}` | `traj_015_ev007` |
| 8 | 42세 +11개월 | 35 | 출산 | `relationship_childbirth` | `{"children_after":[0,9],"dependents_after":2,"family_change_type":"birth"}` | `traj_015_ev008` |
| 9 | 44세 +10개월 | 58 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"child","dependents_after":1,"was_dependent":true}` | `traj_015_ev009` |
| 10 | 45세 +2개월 | 62 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":1000000,"new_stage":"middle"}` | `traj_015_ev010` |
| 11 | 45세 +9개월 | 69 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"인천 연수구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_015_ev011` |
| 12 | 47세 +6개월 | 90 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":1000000,"new_stage":"middle"}` | `traj_015_ev012` |
| 13 | 48세 +5개월 | 101 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":500000,"marital_status_after":"divorced","relationship_transition_type":"divorce"}` | `traj_015_ev013` |
| 14 | 49세 +3개월 | 111 | 고용 종료 | `career_employment_end` | `{"end_reason":"business_closure"}` | `traj_015_ev014` |
| 15 | 49세 +7개월 | 115 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"high"}` | `traj_015_ev016` |
| 16 | 50세 +1개월 | 121 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_015_ev017` |
| 17 | 50세 +4개월 | 124 | 주택 매각 | `housing_home_sale` | `{"ownership_transition":"dispose","post_sale_contract_type":"wolse","post_sale_residence_status":"wolse"}` | `traj_015_ev018` |
| 18 | 51세 +1개월 | 133 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"인천 연수구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":800000,"new_residence_status":"wolse"}` | `traj_015_ev019` |
| 19 | 51세 +1개월 | 133 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"온새미로디자인","new_salary_day":21}` | `traj_015_ev020` |
| 20 | 51세 +3개월 | 135 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_015_ev021` |

## 16. traj_016

- Persona: p_1bcf717903d8 (49세, 직업상태=employed, 혼인=married, 주거=owner, 자녀=2명)
- Horizon: 146개월 (49세 → 61세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 49세 +1개월 | 1 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"family_care"}` | `traj_016_ev001` |
| 2 | 50세 +5개월 | 17 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":21,"previous_employer":"previous_employer"}` | `traj_016_ev002` |
| 3 | 50세 +7개월 | 19 | 본인 교육 시작 | `education_self_program_start` | `{}` | `traj_016_ev003` |
| 4 | 50세 +10개월 | 22 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_016_ev004` |
| 5 | 51세 +2개월 | 26 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":1000000,"new_stage":"primary"}` | `traj_016_ev005` |
| 6 | 51세 +4개월 | 28 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"health"}` | `traj_016_ev006` |
| 7 | 53세 +6개월 | 54 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":200000,"new_stage":"primary"}` | `traj_016_ev007` |
| 8 | 54세 +3개월 | 63 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":25,"previous_employer":"previous_employer"}` | `traj_016_ev008` |
| 9 | 55세 +0개월 | 72 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"서울 마포구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_016_ev010` |
| 10 | 55세 +8개월 | 80 | 은퇴 | `retirement_start` | `{"pension_started_same_time":true,"previous_employment_status":"employed","retirement_reason":"other"}` | `traj_016_ev009` |
| 11 | 55세 +11개월 | 83 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"other_adult","dependents_after":3,"support_amount":200000}` | `traj_016_ev011` |
| 12 | 56세 +0개월 | 84 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":1000000,"new_stage":"middle"}` | `traj_016_ev012` |
| 13 | 57세 +5개월 | 101 | 주택 매각 | `housing_home_sale` | `{"ownership_transition":"dispose","post_sale_contract_type":"family_home","post_sale_residence_status":"family_home"}` | `traj_016_ev013` |
| 14 | 58세 +0개월 | 108 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":200000,"new_stage":"middle"}` | `traj_016_ev015` |
| 15 | 59세 +6개월 | 126 | 이혼/별거 | `relationship_divorce_or_separation` | `{"child_support_amount":500000,"marital_status_after":"divorced","relationship_transition_type":"divorce"}` | `traj_016_ev016` |
| 16 | 59세 +9개월 | 129 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"서울 관악구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_016_ev017` |
| 17 | 60세 +0개월 | 132 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":3000000}` | `traj_016_ev019` |
| 18 | 60세 +2개월 | 134 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":200000,"new_stage":"middle"}` | `traj_016_ev018` |
| 19 | 60세 +6개월 | 138 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":2,"end_reason":"parent_care_end"}` | `traj_016_ev020` |
| 20 | 61세 +2개월 | 146 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":2000000}` | `traj_016_ev021` |

## 17. traj_017

- Persona: p_3b386777f622 (53세, 직업상태=employed, 혼인=widowed, 주거=family_home, 자녀=1명)
- Horizon: 114개월 (53세 → 62세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 53세 +1개월 | 1 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_017_ev001` |
| 2 | 53세 +2개월 | 2 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":200000,"new_stage":"primary"}` | `traj_017_ev002` |
| 3 | 54세 +7개월 | 19 | 자영업 전환 | `career_self_employment` | `{"self_employment_type":"startup"}` | `traj_017_ev003` |
| 4 | 54세 +11개월 | 23 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":4000000}` | `traj_017_ev004` |
| 5 | 55세 +8개월 | 32 | 고용 종료 | `career_employment_end` | `{"end_reason":"business_closure"}` | `traj_017_ev005` |
| 6 | 55세 +11개월 | 35 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":500000}` | `traj_017_ev006` |
| 7 | 56세 +3개월 | 39 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_017_ev008` |
| 8 | 56세 +5개월 | 41 | 연금 수령 시작 | `retirement_pension_start` | `{}` | `traj_017_ev007` |
| 9 | 58세 +2개월 | 62 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"sibling","dependents_after":1,"was_dependent":true}` | `traj_017_ev009` |
| 10 | 58세 +7개월 | 67 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_017_ev010` |
| 11 | 59세 +2개월 | 74 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":500000,"new_stage":"middle"}` | `traj_017_ev011` |
| 12 | 59세 +5개월 | 77 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"한빛물류","new_salary_day":15}` | `traj_017_ev012` |
| 13 | 60세 +1개월 | 85 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_017_ev014` |
| 14 | 60세 +7개월 | 91 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"대성엔지니어링","new_salary_day":25}` | `traj_017_ev015` |
| 15 | 61세 +0개월 | 96 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"서울 마포구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_017_ev016` |
| 16 | 61세 +8개월 | 104 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_017_ev018` |
| 17 | 61세 +10개월 | 106 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"대성엔지니어링","new_salary_day":15}` | `traj_017_ev019` |
| 18 | 61세 +11개월 | 107 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":1200000,"mortgage_payment_day":15,"new_address":"대구 수성구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":false,"post_purchase_residence_status":"owner"}` | `traj_017_ev017` |
| 19 | 62세 +1개월 | 109 | 자녀 교육 단계 진입 | `education_child_stage_entry` | `{"monthly_edu_cost":300000,"new_stage":"high"}` | `traj_017_ev020` |
| 20 | 62세 +6개월 | 114 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":4000000}` | `traj_017_ev021` |

## 18. traj_018

- Persona: p_c2d5f9e29256 (51세, 직업상태=employed, 혼인=divorced, 주거=wolse, 자녀=0명)
- Horizon: 301개월 (51세 → 76세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 51세 +0개월 | 0 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_018_ev001` |
| 2 | 51세 +6개월 | 6 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"parent","dependents_after":1,"support_amount":300000}` | `traj_018_ev002` |
| 3 | 53세 +7개월 | 31 | 고용 종료 | `career_employment_end` | `{"end_reason":"resignation"}` | `traj_018_ev003` |
| 4 | 53세 +9개월 | 33 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_018_ev004` |
| 5 | 54세 +5개월 | 41 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"other_adult","dependents_after":1,"support_amount":500000}` | `traj_018_ev005` |
| 6 | 55세 +6개월 | 54 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"spouse","dependents_after":0,"was_dependent":true}` | `traj_018_ev006` |
| 7 | 56세 +11개월 | 71 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_018_ev008` |
| 8 | 57세 +0개월 | 72 | 이사 | `housing_move` | `{"move_reason":"return_to_family_home","new_address":"경기 수원시 영통구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_018_ev009` |
| 9 | 57세 +1개월 | 73 | 취업 | `career_employment` | `{"employment_transition_type":"new_employment","new_employer":"한빛물류","new_salary_day":25}` | `traj_018_ev007` |
| 10 | 57세 +7개월 | 79 | 은퇴 | `retirement_start` | `{"pension_started_same_time":true,"previous_employment_status":"employed","retirement_reason":"health"}` | `traj_018_ev010` |
| 11 | 59세 +0개월 | 96 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"서울 관악구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_018_ev011` |
| 12 | 63세 +2개월 | 146 | 이사 | `housing_move` | `{"move_reason":"caregiving","new_address":"광주 서구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_018_ev012` |
| 13 | 64세 +4개월 | 160 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_018_ev013` |
| 14 | 65세 +5개월 | 173 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_018_ev014` |
| 15 | 66세 +5개월 | 185 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_018_ev015` |
| 16 | 68세 +3개월 | 207 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"서울 관악구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_018_ev016` |
| 17 | 70세 +0개월 | 228 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"parent","dependents_after":0,"was_dependent":false}` | `traj_018_ev017` |
| 18 | 72세 +4개월 | 256 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"parent","dependents_after":1,"support_amount":300000}` | `traj_018_ev018` |
| 19 | 73세 +0개월 | 264 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"child","dependents_after":0,"was_dependent":true}` | `traj_018_ev019` |
| 20 | 76세 +1개월 | 301 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_018_ev020` |

## 19. traj_019

- Persona: p_d86b1b7a14e2 (59세, 직업상태=employed, 혼인=married, 주거=wolse, 자녀=0명)
- Horizon: 282개월 (59세 → 82세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 59세 +0개월 | 0 | 휴직 시작 | `career_leave_of_absence` | `{"employment_relationship_maintained":true,"leave_reason":"health"}` | `traj_019_ev001` |
| 2 | 59세 +10개월 | 10 | 연금 수령 시작 | `retirement_pension_start` | `{}` | `traj_019_ev002` |
| 3 | 60세 +1개월 | 13 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"other_adult","dependents_after":1,"support_amount":300000}` | `traj_019_ev003` |
| 4 | 60세 +3개월 | 15 | 복직 | `career_reinstatement` | `{"employment_transition_type":"reinstatement","new_employer":"previous_employer","new_salary_day":10,"previous_employer":"previous_employer"}` | `traj_019_ev004` |
| 5 | 61세 +10개월 | 34 | 은퇴 | `retirement_start` | `{"pension_started_same_time":false,"previous_employment_status":"employed","retirement_reason":"health"}` | `traj_019_ev005` |
| 6 | 63세 +6개월 | 54 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_019_ev006` |
| 7 | 64세 +0개월 | 60 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"spouse","dependents_after":0,"was_dependent":true}` | `traj_019_ev007` |
| 8 | 64세 +10개월 | 70 | 이사 | `housing_move` | `{"move_reason":"caregiving","new_address":"대전 유성구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_019_ev008` |
| 9 | 66세 +6개월 | 90 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":3000000}` | `traj_019_ev009` |
| 10 | 67세 +2개월 | 98 | 이사 | `housing_move` | `{"move_reason":"other","new_address":"인천 연수구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":500000,"new_residence_status":"wolse"}` | `traj_019_ev010` |
| 11 | 68세 +6개월 | 114 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"child","dependents_after":0,"was_dependent":false}` | `traj_019_ev011` |
| 12 | 69세 +6개월 | 126 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"other_adult","dependents_after":1,"support_amount":500000}` | `traj_019_ev013` |
| 13 | 69세 +11개월 | 131 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":900000,"mortgage_payment_day":15,"new_address":"광주 서구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":false,"post_purchase_residence_status":"owner"}` | `traj_019_ev012` |
| 14 | 70세 +3개월 | 135 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"광주 서구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":650000,"new_residence_status":"wolse"}` | `traj_019_ev014` |
| 15 | 73세 +0개월 | 168 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"부산 해운대구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_019_ev015` |
| 16 | 74세 +5개월 | 185 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"ordinary","dependent_type":"parent","dependents_after":2,"support_amount":200000}` | `traj_019_ev016` |
| 17 | 76세 +5개월 | 209 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"경기 수원시 영통구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":500000,"new_residence_status":"wolse"}` | `traj_019_ev017` |
| 18 | 77세 +6개월 | 222 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":1500000}` | `traj_019_ev018` |
| 19 | 80세 +11개월 | 263 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"광주 서구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":800000,"new_residence_status":"wolse"}` | `traj_019_ev019` |
| 20 | 82세 +6개월 | 282 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":1500000}` | `traj_019_ev020` |

## 20. traj_020

- Persona: p_8c9a673904c1 (55세, 직업상태=employed, 혼인=married, 주거=family_home, 자녀=1명)
- Horizon: 183개월 (55세 → 70세)
- Occurred life events: 20

| 순서 | 시기 | month_index | life_event | event_id | params | event_instance_id |
|---:|---|---:|---|---|---|---|
| 1 | 55세 +0개월 | 0 | 고용 종료 | `career_employment_end` | `{"end_reason":"job_loss"}` | `traj_020_ev001` |
| 2 | 55세 +5개월 | 5 | 연금 수령 시작 | `retirement_pension_start` | `{}` | `traj_020_ev002` |
| 3 | 55세 +11개월 | 11 | 이사 | `housing_move` | `{"move_reason":"independence","new_address":"대전 유성구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_020_ev003` |
| 4 | 56세 +11개월 | 23 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":500000}` | `traj_020_ev004` |
| 5 | 57세 +6개월 | 30 | 자영업 전환 | `career_self_employment` | `{"self_employment_type":"startup"}` | `traj_020_ev005` |
| 6 | 58세 +1개월 | 37 | 은퇴 | `retirement_start` | `{"pension_started_same_time":false,"previous_employment_status":"self_employed","retirement_reason":"other"}` | `traj_020_ev006` |
| 7 | 59세 +1개월 | 49 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"경기 성남시 분당구","new_contract_type":"jeonse","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"jeonse"}` | `traj_020_ev007` |
| 8 | 60세 +2개월 | 62 | 사고·재난 피해 | `crisis_accident_or_disaster` | `{"one_off_cost":4000000}` | `traj_020_ev008` |
| 9 | 61세 +11개월 | 83 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"인천 연수구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_020_ev009` |
| 10 | 63세 +2개월 | 98 | 주택 구매 | `housing_home_purchase` | `{"loans_after":["mortgage"],"mortgage_monthly":2000000,"mortgage_payment_day":10,"new_address":"인천 연수구","ownership_transition":"acquire","post_purchase_contract_type":"owner","post_purchase_move":false,"post_purchase_residence_status":"owner"}` | `traj_020_ev010` |
| 11 | 63세 +8개월 | 104 | 주택 매각 | `housing_home_sale` | `{"ownership_transition":"dispose","post_sale_contract_type":"other","post_sale_residence_status":"other"}` | `traj_020_ev011` |
| 12 | 63세 +8개월 | 104 | 부양가족 추가 | `relationship_dependent_addition` | `{"cause":"accident","dependent_type":"parent","dependents_after":2,"support_amount":300000}` | `traj_020_ev012` |
| 13 | 63세 +10개월 | 106 | 이사 | `housing_move` | `{"move_reason":"other","new_address":"대구 수성구","new_contract_type":"wolse","new_payee":"집주인","new_rent_amount":650000,"new_residence_status":"wolse"}` | `traj_020_ev013` |
| 14 | 65세 +10개월 | 130 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"accident","dependents_after":1,"end_reason":"parent_care_end"}` | `traj_020_ev014` |
| 15 | 66세 +5개월 | 137 | 이사 | `housing_move` | `{"move_reason":"ordinary_move","new_address":"인천 연수구","new_contract_type":"other","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"other"}` | `traj_020_ev015` |
| 16 | 66세 +9개월 | 141 | 가족 사망 | `relationship_family_death` | `{"deceased_relation":"other","dependents_after":1,"was_dependent":false}` | `traj_020_ev016` |
| 17 | 68세 +0개월 | 156 | 건강 사건 | `crisis_health_event` | `{"one_off_cost":5000000}` | `traj_020_ev020` |
| 18 | 68세 +1개월 | 157 | 부양가족 해소 | `relationship_dependent_end` | `{"cause":"ordinary","dependents_after":0,"end_reason":"parent_care_end"}` | `traj_020_ev019` |
| 19 | 69세 +3개월 | 171 | 이사 | `housing_move` | `{"move_reason":"separate_household","new_address":"서울 관악구","new_contract_type":"family_home","new_payee":"집주인","new_rent_amount":0,"new_residence_status":"family_home"}` | `traj_020_ev021` |
| 20 | 70세 +3개월 | 183 | 금융사기·피싱 피해 | `crisis_financial_fraud` | `{"one_off_cost":1500000}` | `traj_020_ev022` |
