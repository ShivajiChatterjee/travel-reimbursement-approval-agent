from typing import Any, Dict, List

from app.models import TravelClaim


class ResponseBuilder:
    """
    Final response safety layer.

    Design:
    - Gemini writes the final business response and explanation.
    - Python does not rewrite Gemini's explanation.
    - Python only injects deterministic calculation/tool evidence into the final JSON.
    - Fallback response is used only when the final Gemini call fails completely.
    """

    VALID_DECISIONS = {
        "Approve",
        "Partially Approve",
        "Reject",
        "Manual Review",
    }

    def derive_decision(self, tool_output: Dict[str, Any]) -> str:
        """
        Fallback-only decision derivation.

        This is used only if Gemini final response is unavailable
        or returns an invalid decision value.
        """

        submitted_amount = float(tool_output.get("submitted_amount", 0))
        approved_amount = float(tool_output.get("approved_amount", 0))
        rejected_amount = float(tool_output.get("rejected_amount", 0))

        missing_documents = tool_output.get("missing_documents", [])
        manual_review_reasons = tool_output.get("manual_review_reasons", [])
        expense_decisions = tool_output.get("expense_decisions", [])
        tools_called = tool_output.get("tools_called", [])

        has_manual_expense = any(
            self._get_value(decision, "status") == "manual_review"
            for decision in expense_decisions
        )

        approval_threshold_manual_review = False

        for tool in tools_called:
            tool_name = self._get_value(tool, "tool_name")
            details = self._get_value(tool, "details", {}) or {}

            if tool_name == "approval_threshold_tool":
                approval_threshold_manual_review = bool(
                    details.get("manual_review_required", False)
                )

        if (
            missing_documents
            or manual_review_reasons
            or has_manual_expense
            or approval_threshold_manual_review
        ):
            return "Manual Review"

        if approved_amount == 0 and rejected_amount > 0:
            return "Reject"

        if approved_amount > 0 and rejected_amount > 0:
            return "Partially Approve"

        if approved_amount == submitted_amount and rejected_amount == 0:
            return "Approve"

        return "Manual Review"

    def derive_confidence(
        self,
        decision: str,
        tool_output: Dict[str, Any],
    ) -> float:
        """
        Fallback-only confidence.

        Gemini confidence is used when valid.
        This value is used only when Gemini confidence is missing or invalid.
        """

        reason_codes = tool_output.get("reason_codes", [])
        missing_documents = tool_output.get("missing_documents", [])
        manual_review_reasons = tool_output.get("manual_review_reasons", [])

        if decision == "Approve":
            return 0.95

        if decision == "Partially Approve":
            return 0.90

        if decision == "Reject":
            return 0.90

        if decision == "Manual Review":
            if missing_documents or manual_review_reasons:
                return 0.75
            if reason_codes:
                return 0.80
            return 0.70

        return 0.70

    def build_fallback_response(
        self,
        claim: TravelClaim,
        tool_output: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Used only when the final Gemini call fails completely.

        This is not the normal response path.
        Normal response wording comes from Gemini.
        """

        decision = self.derive_decision(tool_output)

        return {
            "claim_id": claim.claim_id,
            "employee_id": claim.employee_id,
            "employee_name": claim.employee_name,
            "decision": decision,
            "submitted_amount": tool_output.get("submitted_amount", 0),
            "approved_amount": tool_output.get("approved_amount", 0),
            "rejected_amount": tool_output.get("rejected_amount", 0),
            "approval_level": tool_output.get("approval_level", "unknown"),
            "missing_documents": tool_output.get("missing_documents", []),
            "manual_review_reasons": tool_output.get("manual_review_reasons", []),
            "policy_references": tool_output.get("policy_references", []),
            "confidence": self.derive_confidence(decision, tool_output),
            "explanation": (
                "Final Gemini response was unavailable. "
                "Deterministic tool results are returned for review."
            ),
            "reason_codes": tool_output.get("reason_codes", []),
            "expense_decisions": tool_output.get("expense_decisions", []),
            "tool_plan": tool_output.get("tool_plan", {}),
            "tools_called": tool_output.get("tools_called", []),
        }

    def repair_with_tool_truth(
        self,
        llm_data: Dict[str, Any],
        claim: TravelClaim,
        tool_output: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Keeps Gemini's final response, but injects Python-calculated evidence.

        Important:
        - Gemini's explanation is preserved.
        - Python does not rewrite the explanation.
        - Python only controls calculated amounts and tool evidence.
        """

        decision = llm_data.get("decision")

        if decision not in self.VALID_DECISIONS:
            decision = self.derive_decision(tool_output)

        confidence = llm_data.get(
            "confidence",
            self.derive_confidence(decision, tool_output),
        )

        try:
            confidence = float(confidence)
        except Exception:
            confidence = self.derive_confidence(decision, tool_output)

        if confidence < 0 or confidence > 1:
            confidence = self.derive_confidence(decision, tool_output)

        explanation = llm_data.get("explanation")

        if not explanation:
            explanation = (
                "Gemini did not return an explanation. "
                "Deterministic tool results are returned for review."
            )

        return {
            "claim_id": claim.claim_id,
            "employee_id": claim.employee_id,
            "employee_name": claim.employee_name,

            # Gemini-owned final recommendation fields
            "decision": decision,
            "confidence": confidence,
            "explanation": explanation,

            # Python-owned deterministic calculation/evidence fields
            "submitted_amount": tool_output.get("submitted_amount", 0),
            "approved_amount": tool_output.get("approved_amount", 0),
            "rejected_amount": tool_output.get("rejected_amount", 0),
            "approval_level": tool_output.get("approval_level", "unknown"),
            "missing_documents": tool_output.get("missing_documents", []),
            "manual_review_reasons": tool_output.get("manual_review_reasons", []),
            "policy_references": tool_output.get("policy_references", []),
            "reason_codes": tool_output.get("reason_codes", []),
            "expense_decisions": tool_output.get("expense_decisions", []),
            "tool_plan": tool_output.get("tool_plan", {}),
            "tools_called": tool_output.get("tools_called", []),
        }

    def _get_value(
        self,
        item: Any,
        key: str,
        default: Any = None,
    ) -> Any:
        """
        Reads a value from either a dict or a Pydantic model.
        """

        if isinstance(item, dict):
            return item.get(key, default)

        return getattr(item, key, default)