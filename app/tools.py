from typing import Any, Dict, List, Optional, Set, Tuple

from app.models import (
    ApprovalMatrix,
    ExpenseDecision,
    ExpenseLimitsConfig,
    PolicyReasoningResponse,
    Receipt,
    ToolResult,
    TravelClaim,
)


ELIGIBLE_CATEGORIES = {"flight", "hotel", "meals", "taxi", "train", "conference"}

NON_REIMBURSABLE_CATEGORIES = {
    "alcohol",
    "personal shopping",
    "entertainment",
    "fines or penalties",
    "family travel",
    "luxury upgrades without approval",
}


def _normalize(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _unique(items: List[str]) -> List[str]:
    seen = set()
    output = []

    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)

    return output


def _to_dict(value: Any) -> Any:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _policy_match_map(
    policy_reasoning: Optional[PolicyReasoningResponse],
) -> Dict[str, Any]:
    if not policy_reasoning:
        return {}

    return {
        match.expense_id: match
        for match in policy_reasoning.expense_policy_matches
    }


def _expense_policy_ids(
    policy_reasoning: Optional[PolicyReasoningResponse],
    expense_id: str,
) -> List[str]:
    match = _policy_match_map(policy_reasoning).get(expense_id)
    return _unique(match.applicable_policy_ids) if match else []


def _all_policy_ids(
    policy_reasoning: Optional[PolicyReasoningResponse],
) -> List[str]:
    if not policy_reasoning:
        return []

    policy_ids: List[str] = []

    for match in policy_reasoning.expense_policy_matches:
        policy_ids.extend(match.applicable_policy_ids)

    policy_ids.extend(policy_reasoning.claim_level_policy_ids)

    return _unique(policy_ids)


def _fallback_policy_ids(
    claim: TravelClaim,
    historical_claims: Optional[List[TravelClaim]] = None,
) -> List[str]:
    categories = {_normalize(expense.category) for expense in claim.expenses}

    policy_ids = ["POL-001", "POL-003", "POL-009", "POL-010", "POL-011", "POL-012"]

    if any(expense.amount > 500 or expense.receipt_id for expense in claim.expenses):
        policy_ids.append("POL-002")

    if any(category in NON_REIMBURSABLE_CATEGORIES for category in categories):
        policy_ids.append("POL-004")

    policy_ids.append("POL-006" if _normalize(claim.travel_type) == "international" else "POL-005")

    if "flight" in categories:
        policy_ids.append("POL-007")

    if historical_claims:
        policy_ids.append("POL-008")

    return _unique(policy_ids)


def _extract_policy_sections(policy_text: str, policy_ids: List[str]) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    current_id = None
    buffer: List[str] = []

    for line in policy_text.splitlines():
        if line.startswith("## POL-"):
            if current_id and buffer:
                sections[current_id] = "\n".join(buffer).strip()

            current_id = line.replace("## ", "").split(":")[0].strip()
            buffer = [line]

        elif current_id:
            buffer.append(line)

    if current_id and buffer:
        sections[current_id] = "\n".join(buffer).strip()

    return {policy_id: sections.get(policy_id, "") for policy_id in policy_ids if policy_id in sections}


def build_tool_plan(
    claim: TravelClaim,
    historical_claims: Optional[List[TravelClaim]] = None,
) -> Dict[str, Any]:
    categories = {_normalize(expense.category) for expense in claim.expenses}

    selected_tools = [
        "policy_lookup_tool",
        "travel_type_validation_tool",
        "expense_eligibility_tool",
        "limit_checker_tool",
        "approval_threshold_tool",
    ]

    if any(expense.amount > 500 or expense.receipt_id for expense in claim.expenses):
        selected_tools.insert(2, "receipt_completeness_tool")

    if historical_claims:
        selected_tools.insert(-2, "duplicate_claim_detector_tool")

    return {
        "selected_tools": selected_tools,
        "expense_categories": sorted(categories),
        "run_policy_lookup": "policy_lookup_tool" in selected_tools,
        "run_travel_type_validation": "travel_type_validation_tool" in selected_tools,
        "run_receipt_check": "receipt_completeness_tool" in selected_tools,
        "run_eligibility_check": "expense_eligibility_tool" in selected_tools,
        "run_duplicate_check": "duplicate_claim_detector_tool" in selected_tools,
        "run_limit_check": "limit_checker_tool" in selected_tools,
        "run_approval_threshold_check": "approval_threshold_tool" in selected_tools,
    }


def policy_lookup_tool(
    claim: TravelClaim,
    policy_text: str,
    historical_claims: Optional[List[TravelClaim]] = None,
    policy_reasoning: Optional[PolicyReasoningResponse] = None,
) -> ToolResult:
    gemini_policy_ids = _all_policy_ids(policy_reasoning)

    if gemini_policy_ids:
        policy_ids = gemini_policy_ids
        source = "gemini_policy_reasoning"
    else:
        policy_ids = _fallback_policy_ids(claim, historical_claims)
        source = "python_fallback"

    if "POL-012" not in policy_ids:
        policy_ids.append("POL-012")

    policy_ids = _unique(policy_ids)

    return ToolResult(
        tool_name="policy_lookup_tool",
        status="completed",
        passed=True,
        details={
            "policy_selection_source": source,
            "policy_context": _extract_policy_sections(policy_text, policy_ids),
            "policy_reasoning": _to_dict(policy_reasoning),
        },
        policy_references=policy_ids,
    )


def travel_type_validation_tool(claim: TravelClaim) -> ToolResult:
    origin = _normalize(claim.origin_country)
    destination = _normalize(claim.destination_country)
    travel_type = _normalize(claim.travel_type)

    manual_review_reasons: List[str] = []
    reason_codes: List[str] = []

    if not origin or not destination:
        manual_review_reasons.append("origin_country or destination_country is missing (POL-001)")
        reason_codes.append("TRAVEL_COUNTRY_MISSING")

    if not travel_type:
        manual_review_reasons.append("travel_type is missing (POL-001)")
        reason_codes.append("TRAVEL_TYPE_MISSING")

    expected = None

    if origin and destination:
        expected = "domestic" if origin == "india" and destination == "india" else "international"

    if expected and travel_type and expected != travel_type:
        manual_review_reasons.append(
            f"travel_type '{claim.travel_type}' does not match origin/destination classification '{expected}' (POL-001)"
        )
        reason_codes.append("TRAVEL_TYPE_MISMATCH")

    passed = not manual_review_reasons

    return ToolResult(
        tool_name="travel_type_validation_tool",
        status="passed" if passed else "manual_review_required",
        passed=passed,
        details={
            "origin_country": claim.origin_country,
            "destination_country": claim.destination_country,
            "provided_travel_type": claim.travel_type,
            "expected_travel_type": expected,
            "manual_review_reasons": manual_review_reasons,
            "reason_codes": reason_codes,
        },
        policy_references=["POL-001"],
    )


def receipt_completeness_tool(
    claim: TravelClaim,
    receipt_map: Dict[str, Receipt],
) -> ToolResult:
    checked_receipts: List[str] = []
    missing_documents: List[str] = []
    mismatches: List[Dict[str, Any]] = []
    manual_review_reasons: List[str] = []
    reason_codes: List[str] = []

    for expense in claim.expenses:
        receipt_required = expense.amount > 500

        if not receipt_required and not expense.receipt_id:
            continue

        if not expense.receipt_id:
            missing_documents.append(f"Missing receipt for expense {expense.expense_id}")
            manual_review_reasons.append(
                f"Receipt is required for expense {expense.expense_id} because amount is greater than INR 500 (POL-002)"
            )
            reason_codes.append("RECEIPT_MISSING")
            continue

        receipt = receipt_map.get(expense.receipt_id)
        checked_receipts.append(expense.receipt_id)

        if not receipt:
            missing_documents.append(f"Receipt ID {expense.receipt_id} not found")
            manual_review_reasons.append(
                f"Receipt ID {expense.receipt_id} referenced by expense {expense.expense_id} was not found (POL-002)"
            )
            reason_codes.append("RECEIPT_NOT_FOUND")
            continue

        if not receipt.attachment_available:
            missing_documents.append(f"Receipt attachment unavailable for {receipt.receipt_id}")
            manual_review_reasons.append(
                f"Receipt attachment is unavailable for expense {expense.expense_id} (POL-002)"
            )
            reason_codes.append("RECEIPT_ATTACHMENT_MISSING")

        mismatch_fields = []

        if _normalize(receipt.vendor) != _normalize(expense.vendor):
            mismatch_fields.append("vendor")
        if receipt.date != expense.date:
            mismatch_fields.append("date")
        if float(receipt.amount) != float(expense.amount):
            mismatch_fields.append("amount")
        if _normalize(receipt.category) != _normalize(expense.category):
            mismatch_fields.append("category")

        if mismatch_fields:
            mismatches.append(
                {
                    "expense_id": expense.expense_id,
                    "receipt_id": receipt.receipt_id,
                    "mismatch_fields": mismatch_fields,
                }
            )
            manual_review_reasons.append(
                f"Receipt details mismatch for expense {expense.expense_id}: {', '.join(mismatch_fields)} (POL-002)"
            )
            reason_codes.append("RECEIPT_MISMATCH")

    passed = not missing_documents and not mismatches and not manual_review_reasons

    return ToolResult(
        tool_name="receipt_completeness_tool",
        status="passed" if passed else "manual_review_required",
        passed=passed,
        details={
            "checked_receipts": checked_receipts,
            "missing_documents": missing_documents,
            "mismatches": mismatches,
            "manual_review_reasons": manual_review_reasons,
            "reason_codes": _unique(reason_codes),
        },
        policy_references=["POL-002"],
    )


def expense_eligibility_tool(
    claim: TravelClaim,
    policy_reasoning: Optional[PolicyReasoningResponse] = None,
) -> ToolResult:
    non_reimbursable_ids: List[str] = []
    unknown_ids: List[str] = []
    manual_review_reasons: List[str] = []
    reason_codes: List[str] = []
    statuses: List[Dict[str, Any]] = []
    policy_refs: List[str] = []

    match_map = _policy_match_map(policy_reasoning)

    true_manual_review_signals = {
        "manual_review",
        "unclear",
        "missing_information",
        "conflicting_information",
        "prior_approval_required",
    }

    for expense in claim.expenses:
        category = _normalize(expense.category)
        match = match_map.get(expense.expense_id)
        policy_ids = _expense_policy_ids(policy_reasoning, expense.expense_id)
        policy_refs.extend(policy_ids)

        decision_signal = ""
        if match:
            decision_signal = _normalize(match.decision_signal)

        if "POL-004" in policy_ids or category in NON_REIMBURSABLE_CATEGORIES:
            status = "non_reimbursable"
            reason = "Expense is mapped to a non-reimbursable category."
            non_reimbursable_ids.append(expense.expense_id)
            reason_codes.append("NON_REIMBURSABLE_EXPENSE")
            policy_ids = _unique(policy_ids + ["POL-004"])

        elif (
            match
            and match.manual_review_required
            and decision_signal in true_manual_review_signals
        ):
            status = "manual_review"
            reason = "Gemini policy reasoning marked this expense for Manual Review."
            manual_review_reasons.append(f"{expense.expense_id}: {reason}")
            reason_codes.append("POLICY_REASONING_MANUAL_REVIEW")

        elif "POL-003" in policy_ids or category in ELIGIBLE_CATEGORIES:
            status = "eligible"
            reason = "Expense is mapped to an eligible travel category."
            policy_ids = _unique(policy_ids + ["POL-003"])

        else:
            status = "manual_review"
            reason = "Expense category could not be confidently mapped to policy."
            unknown_ids.append(expense.expense_id)
            manual_review_reasons.append(f"{expense.expense_id}: {reason}")
            reason_codes.append("UNKNOWN_EXPENSE_CATEGORY")
            policy_ids = _unique(policy_ids + ["POL-003", "POL-004"])

        statuses.append(
            {
                "expense_id": expense.expense_id,
                "category": expense.category,
                "status": status,
                "reason": reason,
                "policy_references": policy_ids,
            }
        )

    passed = not non_reimbursable_ids and not unknown_ids and not manual_review_reasons

    return ToolResult(
        tool_name="expense_eligibility_tool",
        status="passed" if passed else "violations_or_review_found",
        passed=passed,
        details={
            "expense_statuses": statuses,
            "non_reimbursable_expense_ids": non_reimbursable_ids,
            "unknown_expense_ids": unknown_ids,
            "manual_review_reasons": manual_review_reasons,
            "reason_codes": _unique(reason_codes),
        },
        policy_references=_unique(policy_refs + ["POL-003", "POL-004"]),
    )


def _duplicate_key(claim: TravelClaim, expense) -> Tuple[str, str, str, str, float]:
    return (
        claim.employee_id,
        str(expense.date),
        _normalize(expense.vendor),
        _normalize(expense.category),
        float(expense.amount),
    )


def duplicate_claim_detector_tool(
    claim: TravelClaim,
    historical_claims: List[TravelClaim],
) -> ToolResult:
    earlier_claims: List[TravelClaim] = []

    for historical_claim in historical_claims:
        if historical_claim.claim_id == claim.claim_id:
            break
        earlier_claims.append(historical_claim)
    else:
        earlier_claims = historical_claims

    historical_map: Dict[Tuple[str, str, str, str, float], Dict[str, str]] = {}

    for old_claim in earlier_claims:
        for old_expense in old_claim.expenses:
            historical_map[_duplicate_key(old_claim, old_expense)] = {
                "claim_id": old_claim.claim_id,
                "expense_id": old_expense.expense_id,
            }

    duplicate_ids: List[str] = []
    matches: List[Dict[str, Any]] = []

    for expense in claim.expenses:
        key = _duplicate_key(claim, expense)

        if key in historical_map:
            old = historical_map[key]
            duplicate_ids.append(expense.expense_id)
            matches.append(
                {
                    "expense_id": expense.expense_id,
                    "matched_claim_id": old["claim_id"],
                    "matched_expense_id": old["expense_id"],
                }
            )

    passed = not duplicate_ids

    return ToolResult(
        tool_name="duplicate_claim_detector_tool",
        status="passed" if passed else "duplicates_found",
        passed=passed,
        details={
            "duplicate_expense_ids": duplicate_ids,
            "duplicate_matches": matches,
            "reason_codes": ["DUPLICATE_CLAIM"] if duplicate_ids else [],
        },
        policy_references=["POL-008"],
    )


def _policy_limit_conflict(
    claim: TravelClaim,
    policy_ids: List[str],
) -> Optional[str]:
    travel_type = _normalize(claim.travel_type)

    if travel_type == "domestic" and "POL-006" in policy_ids:
        return "Gemini selected international limit policy POL-006 for a domestic claim."

    if travel_type == "international" and "POL-005" in policy_ids:
        return "Gemini selected domestic limit policy POL-005 for an international claim."

    return None


def limit_checker_tool(
    claim: TravelClaim,
    expense_limits: ExpenseLimitsConfig,
    duplicate_expense_ids: Optional[Set[str]] = None,
    policy_reasoning: Optional[PolicyReasoningResponse] = None,
) -> Tuple[ToolResult, List[ExpenseDecision]]:
    duplicate_expense_ids = duplicate_expense_ids or set()

    travel_type = _normalize(claim.travel_type)
    if travel_type not in {"domestic", "international"}:
        travel_type = "domestic"

    limits = expense_limits.domestic if travel_type == "domestic" else expense_limits.international
    match_map = _policy_match_map(policy_reasoning)

    decisions: List[ExpenseDecision] = []
    manual_review_reasons: List[str] = []
    reason_codes: List[str] = []
    policy_refs: List[str] = ["POL-011"]

    true_manual_review_signals = {
        "manual_review",
        "unclear",
        "missing_information",
        "conflicting_information",
        "prior_approval_required",
    }

    for expense in claim.expenses:
        category = _normalize(expense.category)
        submitted = float(expense.amount)
        approved = 0.0
        rejected = 0.0
        status = "approved"
        reason = ""
        refs = _expense_policy_ids(policy_reasoning, expense.expense_id)

        match = match_map.get(expense.expense_id)
        conflict = _policy_limit_conflict(claim, refs)

        decision_signal = ""
        if match:
            decision_signal = _normalize(match.decision_signal)

        if expense.expense_id in duplicate_expense_ids:
            status = "rejected"
            rejected = submitted
            reason = "Expense is a duplicate of an earlier submitted expense."
            refs.append("POL-008")
            reason_codes.append("DUPLICATE_CLAIM")

        elif "POL-004" in refs or category in NON_REIMBURSABLE_CATEGORIES:
            status = "rejected"
            rejected = submitted
            reason = "Expense category is non-reimbursable under policy."
            refs.append("POL-004")
            reason_codes.append("NON_REIMBURSABLE_EXPENSE")

        elif (
            match
            and match.manual_review_required
            and decision_signal in true_manual_review_signals
        ):
            status = "manual_review"
            reason = "Gemini policy reasoning marked this expense for Manual Review."
            manual_review_reasons.append(f"{expense.expense_id}: {reason}")
            reason_codes.append("POLICY_REASONING_MANUAL_REVIEW")

        elif conflict:
            status = "manual_review"
            reason = conflict
            manual_review_reasons.append(f"{expense.expense_id}: {reason}")
            reason_codes.append("POLICY_REASONING_LIMIT_CONFLICT")

        elif category not in limits:
            status = "manual_review"
            reason = "Expense category has no configured calculation rule."
            manual_review_reasons.append(f"{expense.expense_id}: {reason}")
            reason_codes.append("UNKNOWN_EXPENSE_CATEGORY")
            refs.extend(["POL-003", "POL-011"])

        else:
            limit = limits[category]
            refs.append(limit.policy_reference)

            if limit.limit_type == "per_night":
                if not expense.number_of_nights:
                    status = "manual_review"
                    reason = "Hotel claim is missing number_of_nights."
                    manual_review_reasons.append(f"{expense.expense_id}: {reason}")
                    reason_codes.append("HOTEL_NIGHTS_MISSING")
                else:
                    allowed = float(limit.max_amount or 0) * expense.number_of_nights
                    approved = min(submitted, allowed)
                    rejected = max(0.0, submitted - approved)
                    status = "partially_approved" if rejected > 0 else "approved"
                    reason = (
                        f"Hotel limit applied at INR {limit.max_amount} per night "
                        f"for {expense.number_of_nights} night(s)."
                    )
                    if rejected > 0:
                        reason_codes.append("HOTEL_LIMIT_EXCEEDED")

            elif limit.limit_type == "per_day":
                allowed = float(limit.max_amount or 0)
                approved = min(submitted, allowed)
                rejected = max(0.0, submitted - approved)
                status = "partially_approved" if rejected > 0 else "approved"
                reason = f"{expense.category} limit applied at INR {limit.max_amount} per day."
                if rejected > 0:
                    reason_codes.append(f"{category.upper()}_LIMIT_EXCEEDED")

            elif limit.limit_type == "actual_with_receipt":
                approved = submitted
                reason = f"{expense.category} is reimbursable at actual amount with receipt."

            elif limit.limit_type == "economy_only":
                travel_class = _normalize(expense.travel_class)

                if not travel_class:
                    status = "manual_review"
                    reason = "Flight claim is missing travel_class."
                    manual_review_reasons.append(f"{expense.expense_id}: {reason}")
                    reason_codes.append("FLIGHT_CLASS_MISSING")

                elif travel_class != "economy" and not expense.prior_approval_available:
                    status = "manual_review"
                    reason = "Non-economy flight requires prior approval."
                    manual_review_reasons.append(f"{expense.expense_id}: {reason}")
                    reason_codes.append("FLIGHT_PRIOR_APPROVAL_REQUIRED")

                else:
                    approved = submitted
                    reason = "Flight class is eligible for reimbursement."

                refs.append("POL-007")

            else:
                status = "manual_review"
                reason = f"Unsupported limit type: {limit.limit_type}"
                manual_review_reasons.append(f"{expense.expense_id}: {reason}")
                reason_codes.append("UNSUPPORTED_LIMIT_TYPE")

        refs = _unique(refs)
        policy_refs.extend(refs)

        decisions.append(
            ExpenseDecision(
                expense_id=expense.expense_id,
                category=expense.category,
                submitted_amount=submitted,
                approved_amount=approved,
                rejected_amount=rejected,
                status=status,
                reason=reason,
                policy_references=refs,
            )
        )

    total_submitted = sum(item.submitted_amount for item in decisions)
    total_approved = sum(item.approved_amount for item in decisions)
    total_rejected = sum(item.rejected_amount for item in decisions)

    passed = not manual_review_reasons and all(
        item.status == "approved" and item.rejected_amount == 0 for item in decisions
    )

    return (
        ToolResult(
            tool_name="limit_checker_tool",
            status="passed" if passed else "adjustments_or_review_required",
            passed=passed,
            details={
                "submitted_amount": total_submitted,
                "approved_amount": total_approved,
                "rejected_amount": total_rejected,
                "manual_review_reasons": manual_review_reasons,
                "reason_codes": _unique(reason_codes),
            },
            policy_references=_unique(policy_refs),
        ),
        decisions,
    )


def approval_threshold_tool(
    amount_for_approval: float,
    approval_matrix: ApprovalMatrix,
) -> ToolResult:
    selected = None

    for threshold in approval_matrix.approval_thresholds:
        if amount_for_approval >= threshold.min_amount and (
            threshold.max_amount is None or amount_for_approval <= threshold.max_amount
        ):
            selected = threshold
            break

    if not selected:
        return ToolResult(
            tool_name="approval_threshold_tool",
            status="manual_review_required",
            passed=False,
            details={
                "amount_for_approval": amount_for_approval,
                "approval_level": "unknown",
                "manual_review_required": True,
                "manual_review_reasons": ["No approval threshold matched the calculated amount."],
                "reason_codes": ["APPROVAL_THRESHOLD_NOT_FOUND"],
            },
            policy_references=["POL-009"],
        )

    manual_review_reasons = []
    reason_codes = []

    if selected.manual_review_required:
        manual_review_reasons.append(selected.description)
        reason_codes.append("DIRECTOR_APPROVAL_REQUIRED")

    return ToolResult(
        tool_name="approval_threshold_tool",
        status="manual_review_required" if selected.manual_review_required else "passed",
        passed=not selected.manual_review_required,
        details={
            "amount_for_approval": amount_for_approval,
            "approval_level": selected.approval_level,
            "manual_review_required": selected.manual_review_required,
            "description": selected.description,
            "manual_review_reasons": manual_review_reasons,
            "reason_codes": reason_codes,
        },
        policy_references=[selected.policy_reference],
    )


def run_reimbursement_tools(
    claim: TravelClaim,
    policy_text: str,
    receipt_map: Dict[str, Receipt],
    historical_claims: List[TravelClaim],
    approval_matrix: ApprovalMatrix,
    expense_limits: ExpenseLimitsConfig,
    policy_reasoning: Optional[PolicyReasoningResponse] = None,
) -> Dict[str, Any]:
    tool_plan = build_tool_plan(claim, historical_claims)
    tools_called: List[ToolResult] = []

    tools_called.append(policy_lookup_tool(claim, policy_text, historical_claims, policy_reasoning))
    tools_called.append(travel_type_validation_tool(claim))

    if tool_plan["run_receipt_check"]:
        tools_called.append(receipt_completeness_tool(claim, receipt_map))

    tools_called.append(expense_eligibility_tool(claim, policy_reasoning))

    duplicate_ids: Set[str] = set()

    if tool_plan["run_duplicate_check"]:
        duplicate_result = duplicate_claim_detector_tool(claim, historical_claims)
        tools_called.append(duplicate_result)
        duplicate_ids = set(duplicate_result.details.get("duplicate_expense_ids", []))

    limit_result, expense_decisions = limit_checker_tool(
        claim=claim,
        expense_limits=expense_limits,
        duplicate_expense_ids=duplicate_ids,
        policy_reasoning=policy_reasoning,
    )
    tools_called.append(limit_result)

    submitted_amount = sum(expense.amount for expense in claim.expenses)
    approved_amount = sum(item.approved_amount for item in expense_decisions)
    rejected_amount = sum(item.rejected_amount for item in expense_decisions)
    amount_for_approval = approved_amount if approved_amount > 0 else submitted_amount

    tools_called.append(approval_threshold_tool(amount_for_approval, approval_matrix))

    missing_documents: List[str] = []
    manual_review_reasons: List[str] = []
    reason_codes: List[str] = []
    policy_references: List[str] = _all_policy_ids(policy_reasoning)

    if policy_reasoning:
        manual_review_reasons.extend(policy_reasoning.missing_or_conflicting_info)

    for result in tools_called:
        policy_references.extend(result.policy_references)
        missing_documents.extend(result.details.get("missing_documents", []))
        manual_review_reasons.extend(result.details.get("manual_review_reasons", []))
        reason_codes.extend(result.details.get("reason_codes", []))

    for decision in expense_decisions:
        policy_references.extend(decision.policy_references)
        if decision.status == "manual_review":
            manual_review_reasons.append(decision.reason)

    approval_level = "unknown"

    for result in tools_called:
        if result.tool_name == "approval_threshold_tool":
            approval_level = result.details.get("approval_level", "unknown")
            break

    return {
        "tool_plan": tool_plan,
        "policy_reasoning": _to_dict(policy_reasoning),
        "tools_called": tools_called,
        "expense_decisions": expense_decisions,
        "submitted_amount": submitted_amount,
        "approved_amount": approved_amount,
        "rejected_amount": rejected_amount,
        "amount_for_approval": amount_for_approval,
        "approval_level": approval_level,
        "missing_documents": _unique(missing_documents),
        "manual_review_reasons": _unique(manual_review_reasons),
        "policy_references": _unique(policy_references),
        "reason_codes": _unique(reason_codes),
    }


if __name__ == "__main__":
    from app.policy_loader import PolicyDataLoader

    loader = PolicyDataLoader()
    data = loader.load_all()

    sample_claim = loader.get_claim_by_id("CLM-006")

    result = run_reimbursement_tools(
        claim=sample_claim,
        policy_text=data["policy_text"],
        receipt_map=data["receipt_map"],
        historical_claims=data["claims"],
        approval_matrix=data["approval_matrix"],
        expense_limits=data["expense_limits"],
    )

    print("Tools test successful")
    print(f"Claim ID:             {sample_claim.claim_id}")
    print(f"Employee:             {sample_claim.employee_name}")
    print(f"Submitted amount:     {result['submitted_amount']}")
    print(f"Approved amount:      {result['approved_amount']}")
    print(f"Rejected amount:      {result['rejected_amount']}")
    print(f"Approval level:       {result['approval_level']}")
    print(f"Manual review:        {result['manual_review_reasons']}")
    print(f"Reason codes:         {result['reason_codes']}")
    print(f"Tools called:         {[tool.tool_name for tool in result['tools_called']]}")