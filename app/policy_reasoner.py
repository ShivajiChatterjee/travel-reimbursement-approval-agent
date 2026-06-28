import json
from typing import Dict, List, Optional, Set

from app.llm_client import GeminiClient
from app.models import ExpensePolicyMatch, PolicyReasoningResponse, TravelClaim
from app.prompts import TRAVEL_POLICY_INDEX, build_policy_reasoning_prompt


VALID_DECISION_SIGNALS = {
    "eligible",
    "non_reimbursable",
    "limit_check_required",
    "receipt_check_required",
    "duplicate_check_required",
    "manual_review",
    "unclear",
}


class PolicyReasoner:
    """
    LLM-based policy reasoning layer.

    Purpose:
    Gemini reads the submitted claim and the travel policy document.
    Gemini then decides which policy IDs apply to each expense line.

    Important:
    This class does not calculate money.
    It only performs claim-to-policy reasoning.

    Python still validates:
    - policy IDs are real
    - every expense is covered
    - duplicate/missing expense mappings are handled
    - confidence values are valid
    """

    def __init__(
        self,
        policy_text: str,
        llm_client: Optional[GeminiClient] = None,
        use_llm: bool = True,
        model_name: Optional[str] = None,
    ):
        self.policy_text = policy_text

        self.llm_client = llm_client or GeminiClient(
            use_llm=use_llm,
            model_name=model_name,
        )

        self.valid_policy_ids: Set[str] = {
            item["policy_id"] for item in TRAVEL_POLICY_INDEX
        }

        self.last_policy_reasoning_llm_used = False
        self.last_raw_policy_reasoning_response = ""
        self.last_policy_reasoning: Dict = {}

    def reason_over_policy(self, claim: TravelClaim) -> PolicyReasoningResponse:
        """
        Main public method.

        Flow:
        1. Build policy reasoning prompt.
        2. Ask Gemini to map expenses to policy IDs.
        3. Parse and validate Gemini JSON.
        4. Python cleans invalid policy IDs and missing expense mappings.
        5. If Gemini fails, use a safe Manual Review fallback.
        """

        self.last_policy_reasoning_llm_used = False
        self.last_raw_policy_reasoning_response = ""
        self.last_policy_reasoning = {}

        policy_reasoning = None

        if self.llm_client.is_available:
            try:
                prompt = build_policy_reasoning_prompt(
                    claim=claim,
                    policy_text=self.policy_text,
                )

                raw_response = self.llm_client.call_json(prompt)

                self.last_raw_policy_reasoning_response = raw_response

                parsed_response = self.llm_client.extract_json(raw_response)

                policy_reasoning = PolicyReasoningResponse.model_validate(
                    parsed_response
                )

                self.last_policy_reasoning_llm_used = True

            except Exception as error:
                print(
                    "Gemini policy reasoning failed. "
                    f"Using safe policy reasoning fallback. Error: {error}"
                )

        if policy_reasoning is None:
            policy_reasoning = self.build_safe_fallback_reasoning(claim)

        validated_reasoning = self.validate_policy_reasoning(
            claim=claim,
            policy_reasoning=policy_reasoning,
        )

        self.last_policy_reasoning = validated_reasoning.model_dump(mode="json")

        return validated_reasoning

    def validate_policy_reasoning(
        self,
        claim: TravelClaim,
        policy_reasoning: PolicyReasoningResponse,
    ) -> PolicyReasoningResponse:
        """
        Validates Gemini's policy reasoning output.

        This is the safety layer.

        Gemini may suggest policy IDs, but Python verifies:
        - policy IDs are valid
        - expense IDs exist in the claim
        - each expense appears exactly once
        - invalid signals are replaced with unclear/manual_review
        """

        claim_expense_map = {
            expense.expense_id: expense for expense in claim.expenses
        }

        validated_matches: List[ExpensePolicyMatch] = []
        seen_expense_ids: Set[str] = set()
        validation_notes: List[str] = list(
            policy_reasoning.missing_or_conflicting_info
        )

        if policy_reasoning.claim_id != claim.claim_id:
            validation_notes.append(
                f"Gemini returned claim_id {policy_reasoning.claim_id}, "
                f"but expected {claim.claim_id}. Corrected by Python."
            )

        for match in policy_reasoning.expense_policy_matches:
            if match.expense_id not in claim_expense_map:
                validation_notes.append(
                    f"Gemini returned unknown expense_id {match.expense_id}. "
                    "This policy match was ignored."
                )
                continue

            if match.expense_id in seen_expense_ids:
                validation_notes.append(
                    f"Gemini returned duplicate policy reasoning for expense_id "
                    f"{match.expense_id}. Duplicate entry was ignored."
                )
                continue

            claim_expense = claim_expense_map[match.expense_id]

            valid_policy_ids = []
            invalid_policy_ids = []

            for policy_id in match.applicable_policy_ids:
                if policy_id in self.valid_policy_ids:
                    if policy_id not in valid_policy_ids:
                        valid_policy_ids.append(policy_id)
                else:
                    invalid_policy_ids.append(policy_id)

            risk_flags = list(match.risk_flags)

            if invalid_policy_ids:
                risk_flags.append("invalid_policy_id_removed")
                validation_notes.append(
                    f"Invalid policy IDs removed for {match.expense_id}: "
                    f"{invalid_policy_ids}"
                )

            decision_signal = match.decision_signal

            if decision_signal not in VALID_DECISION_SIGNALS:
                validation_notes.append(
                    f"Invalid decision_signal '{decision_signal}' for "
                    f"{match.expense_id}. Changed to unclear."
                )
                decision_signal = "unclear"
                risk_flags.append("invalid_decision_signal")

            manual_review_required = match.manual_review_required

            if not valid_policy_ids:
                manual_review_required = True
                decision_signal = "manual_review"
                risk_flags.append("no_valid_policy_id_selected")
                validation_notes.append(
                    f"No valid policy IDs selected for {match.expense_id}. "
                    "Marked for Manual Review."
                )

            confidence = self._safe_confidence(match.confidence)

            validated_matches.append(
                ExpensePolicyMatch(
                    expense_id=match.expense_id,
                    category=claim_expense.category,
                    applicable_policy_ids=valid_policy_ids,
                    policy_reasoning=match.policy_reasoning,
                    decision_signal=decision_signal,
                    requires_calculation=match.requires_calculation,
                    manual_review_required=manual_review_required,
                    risk_flags=self._unique(risk_flags),
                    confidence=confidence,
                )
            )

            seen_expense_ids.add(match.expense_id)

        for expense in claim.expenses:
            if expense.expense_id not in seen_expense_ids:
                validation_notes.append(
                    f"Gemini did not return policy reasoning for "
                    f"{expense.expense_id}. Added safe Manual Review fallback."
                )

                validated_matches.append(
                    ExpensePolicyMatch(
                        expense_id=expense.expense_id,
                        category=expense.category,
                        applicable_policy_ids=[],
                        policy_reasoning=(
                            "No policy reasoning was returned by Gemini for this "
                            "expense. Manual review is required."
                        ),
                        decision_signal="manual_review",
                        requires_calculation=True,
                        manual_review_required=True,
                        risk_flags=["missing_policy_reasoning"],
                        confidence=0.50,
                    )
                )

        valid_claim_level_policy_ids = []

        for policy_id in policy_reasoning.claim_level_policy_ids:
            if policy_id in self.valid_policy_ids:
                if policy_id not in valid_claim_level_policy_ids:
                    valid_claim_level_policy_ids.append(policy_id)
            else:
                validation_notes.append(
                    f"Invalid claim-level policy ID removed: {policy_id}"
                )

        return PolicyReasoningResponse(
            claim_id=claim.claim_id,
            expense_policy_matches=validated_matches,
            claim_level_policy_ids=valid_claim_level_policy_ids,
            missing_or_conflicting_info=self._unique(validation_notes),
            overall_reasoning=policy_reasoning.overall_reasoning,
            confidence=self._safe_confidence(policy_reasoning.confidence),
        )

    def build_safe_fallback_reasoning(
        self,
        claim: TravelClaim,
    ) -> PolicyReasoningResponse:
        """
        Safe fallback when Gemini policy reasoning is unavailable.

        This fallback does not pretend to make policy decisions.
        It marks expenses for Manual Review because the LLM policy reasoning step failed.
        """

        fallback_matches = []

        for expense in claim.expenses:
            fallback_matches.append(
                ExpensePolicyMatch(
                    expense_id=expense.expense_id,
                    category=expense.category,
                    applicable_policy_ids=[],
                    policy_reasoning=(
                        "Gemini policy reasoning was unavailable. "
                        "Manual review is required before applying policy."
                    ),
                    decision_signal="manual_review",
                    requires_calculation=True,
                    manual_review_required=True,
                    risk_flags=["llm_policy_reasoning_unavailable"],
                    confidence=0.50,
                )
            )

        return PolicyReasoningResponse(
            claim_id=claim.claim_id,
            expense_policy_matches=fallback_matches,
            claim_level_policy_ids=["POL-010", "POL-011", "POL-012"],
            missing_or_conflicting_info=[
                "Gemini policy reasoning was unavailable. "
                "Safe fallback marked expenses for Manual Review."
            ],
            overall_reasoning=(
                "Policy reasoning could not be completed by Gemini, so the claim "
                "requires Manual Review."
            ),
            confidence=0.50,
        )

    @staticmethod
    def _safe_confidence(value) -> float:
        try:
            confidence = float(value)
        except Exception:
            return 0.70

        if confidence < 0:
            return 0.0

        if confidence > 1:
            return 1.0

        return confidence

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        unique_values = []

        for value in values:
            if value and value not in unique_values:
                unique_values.append(value)

        return unique_values


if __name__ == "__main__":
    from app.policy_loader import PolicyDataLoader

    loader = PolicyDataLoader()
    data = loader.load_all()

    sample_claim = loader.get_claim_by_id("CLM-006")

    reasoner = PolicyReasoner(
        policy_text=data["policy_text"],
        use_llm=True,
    )

    result = reasoner.reason_over_policy(sample_claim)

    print("Policy reasoner test completed")
    print("=" * 80)
    print(
        "Gemini policy reasoning:",
        "WORKING" if reasoner.last_policy_reasoning_llm_used else "FALLBACK USED",
    )
    print("=" * 80)
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))