"""Validate generated dialogue sessions against leakage/consistency rules."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from ..fsm.models import LifeEventTemplate
from ..fsm.registry import all_event_labels_ko
from ..io import RepoPaths, load_yaml

_ASSISTANT_LEAK_PATTERNS = [
    "이사하셨군요",
    "결혼하셨군요",
    "출산하셨군요",
    "이직하셨군요",
    "퇴사하셨군요",
    "장례 치르셨군요",
    "신혼이시군요",
]

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F02F\U0001F900-\U0001F9FF❤️]"
)
# 초성체: 2+ consecutive bare jamo (ㅋㅋ, ㅇㅇ, ㄷㄷ ...)
_CHOSEONG_RE = re.compile(r"[ㄱ-ㅎㅏ-ㅣ]{2,}")
_FA_CODE_RE = re.compile(r"FA-\d{2}")

_HIGH_RISK_FA = {"FA-07", "FA-08", "FA-09", "FA-10"}

# Calculation/simulation tasks whose result requires a concrete monetary input
# (principal or monthly deposit) that the user must state. The assistant must not
# present a computed result until that amount is visible in the user's dialogue.
_CALC_TASKS = {
    "적금 만기금액 계산",
    "대출 상환액 시뮬레이션",
    "세후 이자 계산",
    "주택대출 중도상환 시뮬레이션",
}
# Assistant phrasing that presents a calculation as done/shown (vs. still asking).
_CALC_RESULT_MARKERS = (
    "계산한",
    "계산해 드릴게요",
    "계산해드릴게요",
    "만기금액",
    "상환액",
    "예상 이자",
    "세후 이자",
    "결과를",
    "결과가",
    "보여드릴게요",
    "보여 드릴게요",
    "화면에서 확인",
    "화면에 표시",
    "확인하실 수 있어요",
)
# A day-of-month reference ("15일") that is not a duration ("15일간/동안/째").
_DAY_OF_MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*일(?!\s*(?:간|동안|째|치))")
_COMPLETION_RE = re.compile(
    r"(?:완료(?:되었|됐|되었습니다|됐습니다|했|했습니다)|"
    r"처리(?:했|했습니다|되었|됐|되었습니다|됐습니다)|"
    r"접수(?:되었|됐|되었습니다|됐습니다|했|했습니다)|"
    r"적용(?:되었|됐|되었습니다|됐습니다|됩니다|했|했습니다)|"
    r"등록(?:되었|됐|되었습니다|됐습니다|했|했습니다)|"
    r"변경해\s*두었|변경해\s*뒀|해지(?:했|했습니다|되었|됐|되었습니다|됐습니다)|"
    r"실행(?:했|했습니다|되었|됐|되었습니다|됐습니다)|요청을\s*받았습니다|"
    r"다음\s*이체일부터\s*반영)"
)
_CONFIRMATION_PHRASES = ["확인 후", "확인 후에", "동의", "본인 확인"]
_WEAK_SIGNAL_OVERCOMMIT_PHRASES = [
    "이미 확정",
    "확정됐",
    "확정되었",
    "확정되었습니다",
    "확정된 상태",
    "확정된 일정",
    "확정된 건입니다",
    "완전히 확정",
]
_WEAK_SIGNAL_NEGATION_PHRASES = [
    "확정된 건 아닌",
    "확정은 아니",
    "확정된 상태는 아니",
    "확정된 일정은 아니",
    "확정된 것은 아니",
]
_OFFLINE_BANKING_TERMS = [
    "창구",
    "영업점",
    "방문",
    "모시겠습니다",
    "신분증 지참",
    "실물 신분증",
    "신청서",
    "서명",
    "출력",
    "우편 발송",
    "우편 배송",
    "배송",
    "방문 수령",
    "창구 수령",
    "실물 수령",
]

_CONCRETE_NUMBER_RE = re.compile(
    r"(?<![0-9A-Za-z_])(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<unit>천만|백만|십만|억|만|천|백)?(?P<currency>원)?"
)
_KOREAN_NUMBER_MULTIPLIERS = {
    "억": Decimal("100000000"),
    "천만": Decimal("10000000"),
    "백만": Decimal("1000000"),
    "십만": Decimal("100000"),
    "만": Decimal("10000"),
    "천": Decimal("1000"),
    "백": Decimal("100"),
}

# Customers write amounts in Hangul numerals as readily as in digits ("장례비로
# 오백만원 나갔어요"). Digits-only parsing reads those sessions as stating no
# amount at all, so a correctly grounded slot looks ungrounded and gets dropped.
_MONEY_SLOTS = frozenset({"amount", "amount_or_schedule"})

_HANGUL_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_HANGUL_PLACES = {"십": 10, "백": 100, "천": 1000}
_HANGUL_AMOUNT_RE = re.compile(
    r"(?<![0-9가-힣])(?P<head>[일이삼사오육칠팔구십백천]{1,8})\s*"
    r"(?P<scale>억|만)\s*(?:(?P<tail>[일이삼사오육칠팔구십백천]{1,8})\s*)?원"
)


def _hangul_int(text: str) -> int | None:
    """Read a sino-Korean numeral below 10,000: 오백 -> 500, 삼천이백 -> 3200."""
    total = 0
    pending = 0
    for char in text:
        if char in _HANGUL_DIGITS:
            pending = _HANGUL_DIGITS[char]
        elif char in _HANGUL_PLACES:
            # A bare place name means one of it: 백만원 is 1,000,000.
            total += (pending or 1) * _HANGUL_PLACES[char]
            pending = 0
        else:
            return None
    return total + pending or None

_GENERIC_SLOT_VALUES = {
    "해당 금액",
    "정해둔 금액",
    "설정한 금액",
    "선택한 날짜",
    "해당 날짜",
    "매달 초",
    "부모님 계좌",
    "지정 계좌",
}

_VISIBLE_SLOT_ALIASES = {
    "main_checking": ("주거래계좌", "주거래 계좌", "입출금계좌", "입출금 계좌"),
    "spouse": ("배우자", "가족"),
    "KRW": ("원화", "원"),
    "USD": ("미국 달러", "달러", "USD"),
    "JPY": ("일본 엔", "엔화", "JPY"),
    "EUR": ("유로", "EUR"),
}

_LABEL_SUFFIX_RE = r"(?:하|해|했|할|한|하는|하려|를|을|가|이|는|은|로|으로|와|과|에|도|부터|까지|계획|예정)"

# A user can ground an amount by pointing at an arrangement that already exists
# ("금액은 지금 나가는 정도로", "그동안 넣던 금액 그대로") instead of saying the
# number. That is a real dialogue reference, so the value is grounded -- but only
# when the phrase is explicitly about the amount. A bare "그대로" is not enough:
# cancellation sessions say things like "주소는 원래 그대로예요", which refers to
# something else entirely.
_AMOUNT_REFERENCE_RE = re.compile(
    r"(?:금액|액수|얼마)[은는를이]?\s*(?:지금|현재|기존|원래|그동안|예전)?\s*"
    r"(?:나가는|내는|넣던|넣는|하던)?\s*(?:거|것|정도|만큼|대로|그대로)"
    r"|(?:지금|현재|기존|원래|그동안)\s*(?:나가는|내는|넣던|넣는)\s*(?:금액|액수|만큼|정도|거|것)"
    r"|(?:같은|그|해당|기존|원래|동일한)\s*금액"
    r"|나가던\s*만큼|넣던\s*(?:금액|만큼|대로)|금액\s*그대로"
)


def contains_contextual_event_label(text: str, label: str) -> bool:
    """Match a Korean event label as a token/stem, never inside another word."""
    if not text or not label:
        return False
    pattern = re.compile(
        rf"(?<![0-9A-Za-z가-힣]){re.escape(label)}(?=$|[^0-9A-Za-z가-힣]|{_LABEL_SUFFIX_RE})"
    )
    return bool(pattern.search(text))


def policy_claims(text: str) -> set[str]:
    """Normalize the small set of policy claims used by trajectory audits."""
    claims: set[str] = set()
    if re.search(r"(?:공동명의|공동\s*명의).{0,18}(?:가능|등록할 수|만들 수 있)", text):
        claims.add("joint_account:supported")
    if re.search(r"(?:공동명의|공동\s*명의).{0,18}(?:불가능|지원하지 않|만들 수 없)", text):
        claims.add("joint_account:unsupported")
    if re.search(r"(?:대표\s*명의|한\s*명).{0,12}(?:필수|반드시|해야)", text):
        claims.add("joint_account:unsupported")
    return claims


def completion_turn_indices(turns: list[dict[str, Any]]) -> list[int]:
    indices: list[int] = []
    for index, turn in enumerate(turns):
        if turn.get("speaker") != "assistant":
            continue
        text = str(turn.get("text", ""))
        if not _COMPLETION_RE.search(text):
            continue
        if any(
            marker in text
            for marker in (
                "완료되지",
                "처리하지",
                "실행하지",
                "적용되지",
                "반영되지",
                "완료 전",
                "본인인증",
                "인증이 완료",
                "조회가 완료",
                "확인이 완료",
                "안내가 완료",
                "확인 후",
                "승인 후",
                "승인해야",
                "확인 후 진행",
                "승인 후에만",
            )
        ):
            continue
        indices.append(index)
    return indices


def standing_action_amounts(plan: dict[str, Any]) -> frozenset[str]:
    """Canonical amounts of the persona's already-existing standing actions.

    These are the only values an amount reference like "지금 나가는 금액 그대로"
    can legitimately resolve to, so :func:`_slot_value_visible` accepts them
    without the number appearing literally in the dialogue.
    """
    actions = ((plan or {}).get("structured_context") or {}).get(
        "current_standing_actions"
    ) or []
    amounts: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            continue
        amount = action.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float, Decimal)):
            continue
        try:
            amounts.add(_canonical_decimal(Decimal(str(amount))))
        except InvalidOperation:
            continue
    return frozenset(amounts)


def event_slot_candidates(
    plan: dict[str, Any], slot_aliases: dict[str, list[str]]
) -> dict[str, list[Any]]:
    """Per-slot replacement values taken from the triggering event's params.

    The event that drives a session is authoritative for the action it triggers,
    so its params are the right source when the frozen contract grounded a slot
    from the persona's prior state instead. Keyed by execution slot via the same
    ``slot_aliases`` table the planner uses.
    """
    params = (
        ((plan or {}).get("structured_context") or {}).get("event") or {}
    ).get("params") or {}
    if not isinstance(params, dict):
        return {}
    candidates: dict[str, list[Any]] = {}
    for slot, aliases in (slot_aliases or {}).items():
        values = [
            params[alias]
            for alias in aliases
            if alias in params and params[alias] is not None
        ]
        if values:
            candidates[slot] = values
    return candidates


def _slot_value_visible(
    slot: str,
    value: Any,
    user_text: str,
    reference_values: frozenset[str] | set[str] = frozenset(),
) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float, Decimal)):
        try:
            canonical = _canonical_decimal(Decimal(str(value)))
        except InvalidOperation:
            return False
        if canonical in _numbers_in_text(user_text):
            return True
        return bool(
            canonical in reference_values and _AMOUNT_REFERENCE_RE.search(user_text)
        )
    if isinstance(value, str):
        if value in user_text:
            return True
        if any(alias in user_text for alias in _VISIBLE_SLOT_ALIASES.get(value, ())):
            return True
        if slot == "product_or_goal":
            tokens = [
                token
                for token in re.findall(r"[가-힣]{2,}", value)
                if token not in {"설정", "확인", "준비", "변경"}
            ]
            return bool(tokens) and any(token in user_text for token in tokens)
        return False
    if isinstance(value, list):
        return bool(value) and all(_slot_value_visible(slot, item, user_text) for item in value)
    if isinstance(value, dict):
        leaves = [item for item in value.values() if item is not None]
        return bool(leaves) and any(
            _slot_value_visible(slot, item, user_text) for item in leaves
        )
    return False


def _premature_slot_disclosure(
    turns: list[dict[str, Any]], grounded: dict[str, Any]
) -> list[str]:
    """Slots whose value an assistant turn surfaces before any prior user turn.

    Scans the dialogue in order tracking what the user has stated so far. Returns
    the grounded slots (``amount`` and/or ``recurrence_day``) that first appear in
    an assistant turn -- i.e. the bot volunteered the value instead of collecting
    it. Rule-based; reuses the Korean-aware number parser.
    """
    flagged: list[str] = []
    amount = grounded.get("amount")
    day = grounded.get("recurrence_day")
    amount_str = None
    if isinstance(amount, (int, float)):
        amount_str = _canonical_decimal(Decimal(str(int(amount))))
    day_int = int(day) if isinstance(day, (int, float)) else None

    for index, turn in enumerate(turns):
        if turn.get("speaker") != "assistant":
            continue
        prior_user = " ".join(
            str(t.get("text", ""))
            for t in turns[:index]
            if t.get("speaker") == "user"
        )
        text = str(turn.get("text", ""))
        if amount_str is not None and "amount" not in flagged:
            if amount_str in _numbers_in_text(text) and amount_str not in _numbers_in_text(prior_user):
                flagged.append("amount")
        if day_int is not None and "recurrence_day" not in flagged:
            a_days = {int(m) for m in _DAY_OF_MONTH_RE.findall(text)}
            u_days = {int(m) for m in _DAY_OF_MONTH_RE.findall(prior_user)}
            if day_int in a_days and day_int not in u_days:
                flagged.append("recurrence_day")
    return flagged


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _numbers_in_text(text: str) -> list[str]:
    values: list[str] = []
    for match in _CONCRETE_NUMBER_RE.finditer(text):
        try:
            value = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            continue
        unit = match.group("unit")
        if unit:
            value *= _KOREAN_NUMBER_MULTIPLIERS[unit]
        values.append(_canonical_decimal(value))
    for match in _HANGUL_AMOUNT_RE.finditer(text):
        head = _hangul_int(match.group("head"))
        if head is None:
            continue
        total = Decimal(head) * _KOREAN_NUMBER_MULTIPLIERS[match.group("scale")]
        tail = match.group("tail")
        if tail:
            remainder = _hangul_int(tail)
            if remainder is None:
                continue
            total += Decimal(remainder)
        values.append(_canonical_decimal(total))
    return values


def _collect_grounded_numbers(value: Any, result: set[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float, Decimal)):
        try:
            result.add(_canonical_decimal(Decimal(str(value))))
        except InvalidOperation:
            return
        return
    if isinstance(value, str):
        result.update(_numbers_in_text(value))
        return
    if isinstance(value, list):
        for item in value:
            _collect_grounded_numbers(item, result)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_grounded_numbers(item, result)


def grounded_concrete_values(plan: dict[str, Any]) -> set[str]:
    """Return concrete numeric values explicitly available to dialogue.

    Runtime metadata such as month_index and session_id is intentionally
    excluded: it must not accidentally authorize an unrelated amount, date,
    rate, or account suffix in visible dialogue.
    """
    context = plan.get("structured_context") or {}
    event = context.get("event") or {}
    sources = {
        "financial_task": plan.get("financial_task"),
        "must_include_cues": plan.get("must_include_cues") or [],
        "planned_cues": [
            {
                "required_value": cue.get("required_value"),
                "surface_hint": cue.get("surface_hint"),
            }
            for cue in (plan.get("planned_cues") or [])
        ],
        "event_params": event.get("params") or {},
        "current_life_state": (context.get("current_state") or {}).get("life_state") or {},
        "current_financial_memory": context.get("current_financial_memory") or context.get("current_memory") or {},
        "current_standing_actions": context.get("current_standing_actions") or [],
        "action_impacts": context.get("action_impacts") or [],
        "session_memory_updates": context.get("session_memory_updates") or [],
        "event_memory_updates": context.get("event_memory_updates") or [],
        "stale_memory_pairs": plan.get("stale_memory_pairs") or [],
        # The execution contract's grounded_slots are computed from the same
        # structured_context sources above (see evidence_planner.py), so any
        # value the planner already authorized for provided_slots must also
        # be authorized here -- otherwise a session that correctly states its
        # own grounded amount/day gets flagged as a hallucinated value.
        "action_execution_contract_grounded_slots": (
            plan.get("action_execution_contract") or {}
        ).get("grounded_slots") or {},
    }
    result: set[str] = set()
    _collect_grounded_numbers(sources, result)
    return result


def ungrounded_concrete_values(
    visible: str, plan: dict[str, Any], extra_allowed: set[str] | None = None
) -> list[str]:
    grounded = grounded_concrete_values(plan)
    if extra_allowed:
        grounded = grounded | extra_allowed
    ungrounded: list[str] = []
    for value in _numbers_in_text(visible):
        if value not in grounded and value not in ungrounded:
            ungrounded.append(value)
    return ungrounded


def reconcile_provided_slots(
    contract: dict[str, Any],
    resolution: dict[str, Any],
    turns: list[dict[str, Any]],
    slot_candidates: dict[str, list[Any]] | None = None,
    reference_values: frozenset[str] | set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Reconcile an execution contract against the dialogue actually generated.

    Rule-based and deterministic. A grounded slot value survives only when
    :func:`_slot_value_visible` finds it in the user turns -- the same predicate
    the ``provided_slot_not_grounded_in_dialogue`` validator uses -- so the
    reconciled session passes that check by construction.

    The planner grounds slots (e.g. ``amount``) from the persona's structured
    context before the dialogue exists, so a value like a standing transfer's
    amount can be stamped onto a session whose dialogue is about something else
    entirely. For each such slot, in order:

    1. **Re-ground.** If ``slot_candidates[slot]`` holds a value the dialogue
       *does* surface -- typically the triggering event's own parameter -- adopt
       it. The event is authoritative for the action it triggers, so this wins
       even when the grounded value is spoken too: customers narrate both sides
       of a change and the stale half is just as visible as the new one.
    2. **Keep.** The grounded value is spoken as a number, or the user pointed at
       it without saying the number ("금액은 지금 나가는 정도로") and it is one of
       ``reference_values`` -- the persona's existing standing-action amounts.
    3. **Drop.** Move the slot to ``missing_slots`` and, when a required
       execution slot is lost, downgrade the action to
       ``pending_required_information`` (``completion_allowed=False``) and clear
       the completion/confirmation turn indices.

    A required slot the planner never grounded at all is also revisited: when a
    candidate for it *is* spoken, it becomes grounded. The same alias gap that
    made the planner reach for the persona's amount left this slot empty on
    personas that had no standing amount to borrow, so the value is often sitting
    in the transcript unclaimed.

    Returns new ``(contract, resolution, changed_slots)``; the inputs are not
    mutated. ``changed_slots`` lists every slot whose grounding changed, dropped
    or re-grounded -- compare ``grounded_slots`` before and after to tell which.
    A no-op (empty ``changed_slots``) for ``information_only`` contracts and
    sessions already consistent with their dialogue.
    """
    contract = dict(contract or {})
    resolution = dict(resolution or {})
    grounded = dict(contract.get("grounded_slots") or {})
    slot_candidates = slot_candidates or {}
    if not grounded and not slot_candidates:
        return contract, resolution, []

    reference_values = frozenset(reference_values or ())
    user_text = " ".join(
        str(turn.get("text", ""))
        for turn in turns
        if turn.get("speaker") == "user"
    )
    regrounded: dict[str, Any] = {}
    dropped: list[str] = []
    for slot, value in grounded.items():
        # Source priority, mirroring EvidencePlanner._action_execution_contract:
        # the triggering event is authoritative for the action it triggers, so a
        # spoken event param wins even when the currently grounded value is also
        # spoken. Customers narrate both sides of a change ("예전엔 65만원 냈었고
        # 지금은 40만원이에요"), and the stale half is just as visible as the new.
        replacement = next(
            (
                candidate
                for candidate in slot_candidates.get(slot) or ()
                if candidate != value
                and _slot_value_visible(slot, candidate, user_text)
            ),
            None,
        )
        if replacement is not None:
            regrounded[slot] = replacement
        # Then a literally spoken value, and only then one the user merely
        # pointed at -- otherwise a session stating the event's own amount would
        # keep the persona-constant it alluded to with "금액은 지금 나가는 정도로".
        elif _slot_value_visible(slot, value, user_text):
            continue
        elif _slot_value_visible(slot, value, user_text, reference_values):
            continue
        else:
            dropped.append(slot)

    required = list(contract.get("required_slots") or [])
    for slot in required:
        if slot == "explicit_confirmation" or slot in grounded:
            continue
        value = next(
            (
                candidate
                for candidate in slot_candidates.get(slot) or ()
                if _slot_value_visible(slot, candidate, user_text)
            ),
            None,
        )
        if value is not None:
            regrounded[slot] = value

    changed = sorted(dropped + list(regrounded))
    if not changed:
        return contract, resolution, []

    grounded.update(regrounded)
    for slot in dropped:
        grounded.pop(slot, None)
    # Mirror the planner's own missing/readiness definition
    # (EvidencePlanner._action_execution_contract): explicit_confirmation is a
    # confirmation act, never a grounded value, so it never counts as missing.
    missing = [
        slot
        for slot in required
        if slot != "explicit_confirmation" and slot not in grounded
    ]
    ready = not missing

    contract["grounded_slots"] = grounded
    contract["missing_slots"] = missing
    contract["completion_allowed"] = ready
    if contract.get("action_mode") not in (None, "information_only"):
        contract["action_mode"] = (
            "ready_for_confirmation" if ready else "pending_required_information"
        )

    provided = dict(resolution.get("provided_slots") or {})
    for slot in dropped:
        provided.pop(slot, None)
    # Keep provided_slots consistent with grounded_slots: a mismatch is what the
    # high_risk_unplanned_slot_value check flags. Every re-grounded value was
    # found in a user turn, so the customer did supply it -- including slots the
    # frozen resolution had listed as missing.
    provided.update(regrounded)
    resolution["provided_slots"] = provided
    resolution["missing_slots"] = missing
    if not ready:
        resolution["mode"] = "pending_required_information"
        resolution["completion_turn_index"] = None
        resolution["explicit_confirmation_turn_index"] = None

    return contract, resolution, changed


class DialogueValidator:
    def __init__(
        self,
        templates: dict[str, LifeEventTemplate],
        paths: RepoPaths | None = None,
    ):
        self.paths = paths or RepoPaths.default()
        self.templates = templates
        self.event_labels = all_event_labels_ko(templates)
        self.disclosure_registry = load_yaml(
            self.paths.registries / "dialogue_event_disclosure_patterns.yaml"
        )
        self.policy_registry = load_yaml(
            self.paths.registries / "bank_policy_profile.yaml"
        )
        self.cfg = load_yaml(self.paths.generation / "dialogue.yaml")

    def validate_session(self, session: dict[str, Any]) -> list[dict[str, str]]:
        """Return a list of violations: [{code, detail}]."""
        violations: list[dict[str, str]] = []

        def flag(code: str, detail: str) -> None:
            violations.append({"code": code, "detail": detail})

        turns = session.get("turns") or []
        if not turns:
            flag("no_turns", "session has no turns")
            return violations
        if turns[0].get("speaker") != "user":
            flag("first_turn_not_user", "first turn is not user")
        if turns[-1].get("speaker") != "assistant":
            flag("last_turn_not_assistant", "last turn is not assistant")

        # speaker alternation
        for i in range(1, len(turns)):
            if turns[i]["speaker"] == turns[i - 1]["speaker"]:
                flag("speaker_alternation", f"turns {i-1}/{i} same speaker")
                break

        visible = " ".join(t.get("text", "") for t in turns)
        user_text = " ".join(t.get("text", "") for t in turns if t.get("speaker") == "user")
        assistant_text = " ".join(t.get("text", "") for t in turns if t.get("speaker") == "assistant")

        # leakage: skip labels that are substrings of this session's own
        # required cues (financial-consequence cues may share words with a
        # composite label, e.g. '수술비 수납' vs '수술')
        plan_cues = (session.get("plan") or {}).get("must_include_cues") or []
        for label in self.event_labels:
            if any(label in cue for cue in plan_cues):
                continue
            if contains_contextual_event_label(visible, label):
                flag("event_label_leakage", f"label '{label}' in visible text")
        if _FA_CODE_RE.search(visible):
            flag("fa_code_leakage", "FA-XX code in visible text")
        for phrase in _ASSISTANT_LEAK_PATTERNS:
            if phrase in assistant_text:
                flag("assistant_event_summary", f"assistant says '{phrase}'")
        if _EMOJI_RE.search(visible):
            flag("emoji", "emoji in visible text")
        if _CHOSEONG_RE.search(visible):
            flag("choseongche", "초성체 in visible text")
        for term in _OFFLINE_BANKING_TERMS:
            if term in visible:
                flag("offline_banking_context", f"offline/branch term '{term}' in visible text")
                break

        # cue annotations must point at user turns
        plan = session.get("plan") or {}
        target_memory_paths = set(plan.get("target_memory_paths") or [])
        cue_annotations = session.get("cue_annotations") or []
        annotated_user_texts: list[str] = []
        for cue in cue_annotations:
            idx = cue.get("turn_index", -1)
            if not (0 <= idx < len(turns)):
                flag("cue_index_out_of_range", f"cue turn_index {idx}")
            elif turns[idx]["speaker"] != "user":
                flag("cue_not_user_turn", f"cue at turn {idx} is not a user turn")
            else:
                annotated_user_texts.append(turns[idx].get("text", ""))
                cue_text = cue.get("evidence_text") or cue.get("cue_text")
                if cue_text and cue_text not in turns[idx].get("text", ""):
                    flag("cue_text_not_in_annotated_turn", f"evidence text '{cue_text}' absent from turn {idx}")
            linked = cue.get("linked_memory_path")
            if linked is not None and linked not in target_memory_paths:
                flag("cue_linked_path_not_target", f"linked_memory_path '{linked}' not in target_memory_paths")
        if plan.get("must_include_cues") and not cue_annotations:
            flag("missing_cue_annotation", "must_include_cues present but cue_annotations is empty")

        expected_memory_facts = (
            (plan.get("structured_context") or {}).get("session_memory_updates") or []
        )
        for expected in expected_memory_facts:
            matching = [
                cue for cue in cue_annotations
                if cue.get("cue_type") == "memory_fact"
                and cue.get("linked_memory_path") == expected.get("path")
                and cue.get("linked_memory_operation") == expected.get("operation")
                and cue.get("linked_memory_value") == expected.get("new_value")
            ]
            if not matching:
                flag(
                    "missing_memory_fact_grounding",
                    f"{expected.get('path')} {expected.get('operation')} lacks exact annotation",
                )

        status = session.get("event_status_after_session", "no_event")
        session_type = session.get("session_type", "")
        if session_type == "stale_recall_session":
            for pair in plan.get("stale_memory_pairs") or []:
                path = pair.get("path")
                old_grounded = any(
                    cue.get("cue_type") == "stale_value"
                    and cue.get("linked_memory_path") == path
                    and cue.get("linked_memory_value") == pair.get("old_value")
                    and cue.get("evidence_text")
                    for cue in cue_annotations
                )
                current_grounded = any(
                    cue.get("cue_type") == "current_value"
                    and cue.get("linked_memory_path") == path
                    and cue.get("linked_memory_value") == pair.get("current_value")
                    and cue.get("evidence_text")
                    for cue in cue_annotations
                )
                if not (old_grounded and current_grounded):
                    flag("stale_old_current_confusion", str(path))
        evidence_types = {
            "weak_signal_evidence",
            "upcoming_evidence",
            "occurred_evidence",
            "cancellation_evidence",
        }

        if session_type in evidence_types:
            opening = str(turns[0].get("text", ""))
            task_terms = [
                token
                for token in re.findall(r"[가-힣]{2,}", str(plan.get("financial_task") or session.get("financial_task") or ""))
                if token not in {"확인", "설정", "관리", "내역", "관련"}
            ]
            task_annotated = any(
                cue.get("turn_index") == 0 and cue.get("cue_type") == "task_intent"
                for cue in cue_annotations
            )
            if not task_annotated and not any(term in opening for term in task_terms):
                flag(
                    "task_not_introduced_in_opening",
                    "first user turn does not introduce the planned banking task",
                )

            dimensions = plan.get("evidence_dimensions") or []
            dimension_by_id = {
                str(item.get("dimension_id")): item for item in dimensions
            }
            realized: dict[str, list[int]] = {}
            role_realized: dict[str, list[int]] = {}
            for cue in cue_annotations:
                dimension_id = cue.get("evidence_dimension_id")
                if not dimension_id:
                    continue
                if dimension_id not in dimension_by_id:
                    flag(
                        "evidence_dimension_annotation_mismatch",
                        f"unknown evidence dimension '{dimension_id}'",
                    )
                    continue
                expected_role = str(dimension_by_id[dimension_id].get("role"))
                if cue.get("cue_type") not in {expected_role, "memory_fact"}:
                    flag(
                        "evidence_dimension_annotation_mismatch",
                        f"{dimension_id} annotated as {cue.get('cue_type')}, expected {expected_role}",
                    )
                index = int(cue.get("turn_index", -1))
                if 0 <= index < len(turns) and turns[index].get("speaker") == "user":
                    realized.setdefault(str(dimension_id), []).append(index)
                    if cue.get("cue_type") == expected_role:
                        role_realized.setdefault(str(dimension_id), []).append(index)
            required_dimensions = [
                item for item in dimensions if item.get("required", True)
            ]
            missing_dimensions = [
                item
                for item in required_dimensions
                if str(item.get("dimension_id")) not in realized
            ]
            if missing_dimensions:
                missing_ids = [str(item.get("dimension_id")) for item in missing_dimensions]
                flag("required_evidence_not_realized", ", ".join(missing_ids))
                flag("insufficient_event_evidence", ", ".join(missing_ids))
            required_roles = {
                str(item.get("role")) for item in required_dimensions
            }
            realized_roles = {
                str(dimension_by_id[dimension_id].get("role"))
                for dimension_id in realized
                if dimension_id in dimension_by_id
            }
            for role in sorted(required_roles - realized_roles):
                flag("missing_required_evidence_role", role)
            if "subtype_disambiguation" in required_roles - realized_roles:
                flag("subtype_not_disambiguated", "subtype-disambiguation evidence missing")
            if dimensions and not realized_roles.intersection(
                {"state_change", "entity_change", "subtype_disambiguation", "uncertainty", "future_timing", "cancellation", "prior_current_contrast"}
            ):
                flag("generic_financial_task_only", "only generic financial activity is evidenced")

            realized_turns = sorted(
                {
                    turn
                    for dimension_id, values in realized.items()
                    for turn in (role_realized.get(dimension_id) or values)
                }
            )
            planned_slots = set(plan.get("evidence_placement_slots") or [])
            placement_strategy = str(plan.get("evidence_placement_strategy") or "")
            placement_matches = True
            if realized_turns and planned_slots:
                if placement_strategy == "task_first_evidence_split_turns_2_3":
                    placement_matches = planned_slots.issubset(realized_turns)
                elif placement_strategy == "final_user_turn_reveal":
                    placement_matches = set(realized_turns).issubset(planned_slots)
                else:
                    placement_matches = min(realized_turns) == min(planned_slots)
            if realized_turns and planned_slots and not placement_matches:
                flag(
                    "evidence_placement_strategy_mismatch",
                    f"realized at {realized_turns}, planned {sorted(planned_slots)}",
                )
            dialogue_contract = (plan.get("structured_context") or {}).get("dialogue_contract") or {}
            final_reveal = bool(dialogue_contract.get("explicit_final_reveal"))
            deadline = int(
                dialogue_contract.get(
                    "evidence_deadline_user_turn",
                    self.cfg.get("semantic_validity", {}).get("evidence_deadline_user_turn", 3),
                )
            )
            user_indices = [index for index, turn in enumerate(turns) if turn.get("speaker") == "user"]
            deadline_index = user_indices[min(max(deadline, 1), len(user_indices)) - 1]
            if realized_turns and not final_reveal and max(realized_turns) > deadline_index:
                flag(
                    "evidence_revealed_too_late",
                    f"evidence after user turn {deadline}",
                )

        event_id = str(((plan.get("structured_context") or {}).get("event") or {}).get("event_id") or "")
        disclosure = self.disclosure_registry.get(event_id) or {}
        direct_patterns = list(plan.get("forbidden_direct_event_patterns") or [])
        direct_patterns.extend(disclosure.get("disallowed") or [])
        status_exceptions = disclosure.get("status_exceptions") or {}
        exception_spec = status_exceptions.get(status) or {}
        exception_patterns = set(
            exception_spec.get("allowed_direct_patterns") or []
            if isinstance(exception_spec, dict)
            else exception_spec or []
        )
        for pattern in dict.fromkeys(direct_patterns):
            if pattern and pattern not in exception_patterns and pattern in visible:
                flag("direct_event_disclosure", f"direct pattern '{pattern}'")
                flag("forbidden_event_paraphrase", f"forbidden paraphrase '{pattern}'")
        for pattern in disclosure.get("review_only") or []:
            if pattern and pattern in visible:
                flag("near_direct_event_disclosure", f"borderline pattern '{pattern}'")

        if session_type == "hard_negative" or plan.get("expected_memory_operation") == "no_update":
            unexpected_facts = [
                cue for cue in cue_annotations
                if cue.get("cue_type") == "memory_fact"
                or cue.get("linked_memory_operation") not in {None, "no_update"}
            ]
            if unexpected_facts:
                flag(
                    "hard_negative_unintended_update",
                    "no-update session contains a memory fact or mutation annotation",
                )

        # In a calculation/simulation session the user supplies a hypothetical
        # principal/monthly amount that is not (and should not be) grounded in
        # planned memory -- it is a legitimate user input, not a hallucinated
        # fact. Allow concrete numbers the user introduces; the assistant is
        # still held to grounded values.
        extra_allowed = None
        if session.get("financial_task") in _CALC_TASKS:
            extra_allowed = set(_numbers_in_text(user_text))
        invented_values = ungrounded_concrete_values(visible, plan, extra_allowed)
        if invented_values:
            flag(
                "concrete_value_hallucination",
                ", ".join(invented_values[:8]),
            )

        # required cues present / forbidden absent
        for cue in plan.get("must_include_cues") or []:
            if cue and cue not in visible:
                flag("missing_required_cue", f"cue '{cue}' absent")
            if cue and cue in visible and cue not in user_text:
                flag("required_cue_not_in_user_turn", f"cue '{cue}' absent from user turns")
            if cue and cue in visible and not any(cue in text for text in annotated_user_texts):
                flag("required_cue_not_annotated", f"cue '{cue}' not present in any annotated user turn")
        for term in plan.get("must_not_include_terms") or []:
            prohibited = (
                contains_contextual_event_label(visible, term)
                if term in self.event_labels
                else bool(term and term in visible)
            )
            if prohibited:
                flag("forbidden_term", f"term '{term}' present")

        # status consistency
        if session_type in {"hard_negative", "routine_financial"} and status != "no_event":
            flag("status_inconsistent", f"{session_type} with status {status}")
        if status == "occurred" and session_type == "occurred_evidence":
            if not (session.get("cue_annotations") or []):
                flag("occurred_without_consequence_cue", "occurred_evidence session has no cue annotation")
        if status == "weak_signal":
            if (
                any(phrase in visible for phrase in _WEAK_SIGNAL_OVERCOMMIT_PHRASES)
                and not any(phrase in visible for phrase in _WEAK_SIGNAL_NEGATION_PHRASES)
            ):
                flag("weak_signal_overcommitted", "weak_signal session implies confirmation")

        # High-risk completion is legal only when the planner supplied every
        # concrete slot and the user explicitly confirmed in a visible turn.
        mapped = session.get("mapped_action")
        if mapped in _HIGH_RISK_FA:
            contract = plan.get("action_execution_contract") or {}
            resolution = session.get("action_resolution") or {}
            completion_indices = completion_turn_indices(turns)
            attempted_completion = bool(completion_indices) or resolution.get("mode") == "executed_after_confirmation"
            confirmation_index = resolution.get("explicit_confirmation_turn_index")
            visible_confirmation = (
                isinstance(confirmation_index, int)
                and 0 <= confirmation_index < len(turns)
                and turns[confirmation_index].get("speaker") == "user"
                and any(
                    phrase in str(turns[confirmation_index].get("text", ""))
                    for phrase in ("확인", "동의", "승인", "진행해", "실행해", "해주세요", "해 주세요")
                )
            )
            provided = resolution.get("provided_slots") or {}
            grounded = contract.get("grounded_slots") or {}
            # An amount the user pointed at rather than spelled out ("금액은 지금
            # 나가는 정도로") is grounded in the dialogue too, but only against the
            # standing actions that already exist for this persona.
            reference_values = standing_action_amounts(plan)
            for slot, value in provided.items():
                if (
                    (isinstance(value, str) and value in _GENERIC_SLOT_VALUES)
                    or slot not in grounded
                    or grounded.get(slot) != value
                ):
                    flag("high_risk_unplanned_slot_value", f"{slot}={value!r}")
            missing = list(contract.get("missing_slots") or [])
            runtime_missing = [
                slot
                for slot in contract.get("required_slots") or []
                if slot != "explicit_confirmation"
                and (
                    slot not in grounded
                    or slot not in provided
                    or provided.get(slot) != grounded.get(slot)
                    or not _slot_value_visible(
                        slot, grounded.get(slot), user_text, reference_values
                    )
                )
            ]
            # NEW: catch ungrounded provided_slots even when the session never
            # claims completion (pending_required_information sessions were
            # previously exempt from this check entirely, since the flags below
            # only fire when attempted_completion is True).
            ungrounded_provided = [
                slot
                for slot in provided
                if slot in grounded
                and not _slot_value_visible(
                    slot, grounded.get(slot), user_text, reference_values
                )
            ]
            # The check above walks provided_slots, so a money slot the planner
            # grounded from the event but the resolution reports as missing slips
            # through and the amount is silently lost. Grounding it means the
            # event fixes that amount for this session, so the customer has to
            # say it -- "그 금액 맞아요" or "네 그 정도로" does not.
            unstated_amounts = [
                slot
                for slot, value in grounded.items()
                if slot in _MONEY_SLOTS
                and not _slot_value_visible(slot, value, user_text, reference_values)
            ]
            for slot in unstated_amounts:
                flag(
                    "grounded_amount_not_stated",
                    f"{slot}={grounded.get(slot)!r} never stated by the customer",
                )
            if ungrounded_provided:
                flag(
                    "provided_slot_not_grounded_in_dialogue",
                    ", ".join(sorted(ungrounded_provided)),
                )
            # Premature disclosure: the assistant must collect execution values
            # from the user, never volunteer them. Flag when an assistant turn
            # first surfaces the grounded amount or recurrence day before any
            # earlier user turn has stated it.
            premature = _premature_slot_disclosure(turns, grounded)
            for slot in premature:
                flag("assistant_premature_slot_disclosure", slot)
            if attempted_completion and not contract.get("required_slots") and contract.get("action_mode") != "information_only":
                flag("high_risk_missing_required_slot", "execution contract has no required slots")
            if attempted_completion and (missing or runtime_missing):
                flag(
                    "high_risk_missing_required_slot",
                    ", ".join(sorted(set(missing + runtime_missing))),
                )
            if attempted_completion and contract.get("confirmation_required") and not visible_confirmation:
                flag("high_risk_missing_confirmation", "completion lacks visible explicit user confirmation")
            if (
                completion_indices
                and visible_confirmation
                and int(confirmation_index) >= min(completion_indices)
            ):
                flag("high_risk_missing_confirmation", "confirmation does not precede completion")
            if attempted_completion and (
                not contract.get("completion_allowed")
                or missing
                or runtime_missing
                or (contract.get("confirmation_required") and not visible_confirmation)
            ):
                flag("high_risk_false_completion", f"completion at turns {completion_indices}")
                flag("high_risk_auto_execution", f"unsafe completion at turns {completion_indices}")
            recorded_completion = resolution.get("completion_turn_index")
            if (recorded_completion is None) != (not completion_indices) or (
                completion_indices and recorded_completion not in completion_indices
            ):
                flag("high_risk_action_resolution_mismatch", "completion index disagrees with visible dialogue")
            expected_mode = (
                "executed_after_confirmation"
                if completion_indices
                else contract.get("action_mode", "information_only")
            )
            if resolution.get("mode", "information_only") != expected_mode:
                flag(
                    "high_risk_action_resolution_mismatch",
                    f"mode {resolution.get('mode')!r}, expected {expected_mode!r}",
                )
            expected_missing = sorted(missing)
            if sorted(resolution.get("missing_slots") or []) != expected_missing:
                flag("high_risk_action_resolution_mismatch", "missing slots disagree with plan contract")

        # Calculation sessions must obtain the required monetary input (principal
        # or monthly deposit) from the user before presenting a result. Flag when
        # the assistant presents a computed result but no KRW amount appears in
        # the user's turns.
        if session.get("financial_task") in _CALC_TASKS:
            if any(marker in assistant_text for marker in _CALC_RESULT_MARKERS):
                user_amounts = [n for n in _numbers_in_text(user_text) if int(n) >= 10000]
                if not user_amounts:
                    flag(
                        "calc_result_without_required_input",
                        f"{session.get('financial_task')}: no principal/deposit amount stated by user",
                    )

        policy_rules = (self.policy_registry.get("rules") or {})
        for policy_key, rule in policy_rules.items():
            if not isinstance(rule, dict):
                continue
            for pattern in rule.get("prohibited_patterns") or []:
                if pattern and pattern in assistant_text:
                    flag("unsupported_bank_policy_claim", f"{policy_key}: '{pattern}'")
        normalized_claims = policy_claims(assistant_text)
        if {
            "joint_account:supported",
            "joint_account:unsupported",
        }.issubset(normalized_claims):
            flag("bank_policy_contradiction", "contradictory joint-account claims")

        return violations


def summarize_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    failed = [r for r in results if r["violations"]]
    by_code: dict[str, int] = {}
    for r in failed:
        for v in r["violations"]:
            by_code[v["code"]] = by_code.get(v["code"], 0) + 1
    return {
        "total_sessions": total,
        "sessions_with_violations": len(failed),
        "pass_rate": round(1 - len(failed) / total, 4) if total else None,
        "violations_by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
    }
