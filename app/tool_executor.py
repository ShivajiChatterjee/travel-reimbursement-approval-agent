import inspect
from typing import Any, Dict, List, Optional, Set

from app.models import (
    ExpenseDecision,
    PolicyReasoningResponse,
    Receipt,
    ToolResult,
    TravelClaim,
)
from app.tools import (
    approval_threshold_tool,
    duplicate_claim_detector_tool,
    expense_eligibility_tool,
    limit_checker_tool,
    policy_lookup_tool,
    receipt_completeness_tool,
    travel_type_validation_tool,
)


class ToolExecutor:
    """
    Executes deterministic reimbursement tools in validated order.

    New architecture role:
    - Gemini performs policy reasoning before this step.
    - Python executes deterministic tools and calculations in this step.
    - The tool output includes both Gemini policy reasoning and Python tool results.
    - Final Gemini step can then semantically validate policy reasoning + tool evidence.
    """

    def __init__(
        self,
        policy_text: str,
        receipt_map: Dict[str, Receipt],
        historical_claims: List[TravelClaim],
        approval_matrix: Any,
        expense_limits: Any,
    ):
        self.policy_text = policy_text
        self.receipt_map = receipt_map
        self.historical_claims = historical_claims
        self.approval_matrix = approval_matrix
        self.expense_limits = expense_limits

    def execute(
        self,
        claim: TravelClaim,
        llm_tool_plan: Any,
        validated_tool_plan: Dict[str, Any],
        policy_reasoning: Optional[PolicyReasoningResponse] = None,
    ) -> Dict[str, Any]:
        """
        Runs tools in dependency-safe order and returns aggregated tool output.

        policy_reasoning:
            Gemini's claim-to-policy mapping generated before tool execution.
            It is passed into the evidence package for final semantic validation.
        """

        final_execution_order = validated_tool_plan["final_execution_order"]

        tools_called: List[ToolResult] = []
        duplicate_expense_ids: Set[str] = set()
        expense_decisions: List[ExpenseDecision] = []

        policy_reasoning_dict = self._model_to_dict(policy_reasoning)

        policy_reasoning_policy_refs = self._extract_policy_refs_from_reasoning(
            policy_reasoning
        )

        policy_reasoning_manual_review_reasons = (
            self._extract_manual_review_from_reasoning(policy_reasoning)
        )

        for tool_name in final_execution_order:
            if tool_name == "policy_lookup_tool":
                result = self._call_tool(
                    policy_lookup_tool,
                    policy_reasoning=policy_reasoning,
                    claim=claim,
                    policy_text=self.policy_text,
                    historical_claims=self.historical_claims,
                )
                tools_called.append(result)

            elif tool_name == "travel_type_validation_tool":
                result = self._call_tool(
                    travel_type_validation_tool,
                    policy_reasoning=policy_reasoning,
                    claim=claim,
                )
                tools_called.append(result)

            elif tool_name == "receipt_completeness_tool":
                result = self._call_tool(
                    receipt_completeness_tool,
                    policy_reasoning=policy_reasoning,
                    claim=claim,
                    receipt_map=self.receipt_map,
                )
                tools_called.append(result)

            elif tool_name == "expense_eligibility_tool":
                result = self._call_tool(
                    expense_eligibility_tool,
                    policy_reasoning=policy_reasoning,
                    claim=claim,
                )
                tools_called.append(result)

            elif tool_name == "duplicate_claim_detector_tool":
                result = self._call_tool(
                    duplicate_claim_detector_tool,
                    policy_reasoning=policy_reasoning,
                    claim=claim,
                    historical_claims=self.historical_claims,
                )
                tools_called.append(result)

                duplicate_expense_ids = set(
                    result.details.get("duplicate_expense_ids", [])
                )

            elif tool_name == "limit_checker_tool":
                result, expense_decisions = self._call_tool(
                    limit_checker_tool,
                    policy_reasoning=policy_reasoning,
                    claim=claim,
                    expense_limits=self.expense_limits,
                    duplicate_expense_ids=duplicate_expense_ids,
                )
                tools_called.append(result)

            elif tool_name == "approval_threshold_tool":
                submitted_amount = sum(expense.amount for expense in claim.expenses)
                approved_amount = sum(
                    decision.approved_amount for decision in expense_decisions
                )

                amount_for_approval = (
                    approved_amount if approved_amount > 0 else submitted_amount
                )

                result = self._call_tool(
                    approval_threshold_tool,
                    policy_reasoning=policy_reasoning,
                    amount_for_approval=amount_for_approval,
                    approval_matrix=self.approval_matrix,
                )
                tools_called.append(result)

        submitted_amount = sum(expense.amount for expense in claim.expenses)
        approved_amount = sum(decision.approved_amount for decision in expense_decisions)
        rejected_amount = sum(decision.rejected_amount for decision in expense_decisions)

        amount_for_approval = approved_amount if approved_amount > 0 else submitted_amount

        missing_documents: List[str] = []
        manual_review_reasons: List[str] = []
        reason_codes: List[str] = []
        policy_references: List[str] = []

        policy_references.extend(policy_reasoning_policy_refs)
        manual_review_reasons.extend(policy_reasoning_manual_review_reasons)

        for result in tools_called:
            policy_references.extend(result.policy_references)
            missing_documents.extend(result.details.get("missing_documents", []))
            manual_review_reasons.extend(result.details.get("manual_review_reasons", []))
            reason_codes.extend(result.details.get("reason_codes", []))

        for decision in expense_decisions:
            policy_references.extend(decision.policy_references)

        approval_level = "unknown"

        for result in tools_called:
            if result.tool_name == "approval_threshold_tool":
                approval_level = result.details.get("approval_level", "unknown")
                break

        return {
            "llm_tool_plan": self._model_to_dict(llm_tool_plan),
            "validated_tool_plan": validated_tool_plan,
            "policy_reasoning": policy_reasoning_dict,
            "tool_plan": validated_tool_plan,
            "tools_called": tools_called,
            "expense_decisions": expense_decisions,
            "submitted_amount": submitted_amount,
            "approved_amount": approved_amount,
            "rejected_amount": rejected_amount,
            "amount_for_approval": amount_for_approval,
            "approval_level": approval_level,
            "missing_documents": self._unique(missing_documents),
            "manual_review_reasons": self._unique(manual_review_reasons),
            "policy_references": self._unique(policy_references),
            "reason_codes": self._unique(reason_codes),
        }

    def _call_tool(
        self,
        tool_function,
        policy_reasoning: Optional[PolicyReasoningResponse] = None,
        **kwargs,
    ):
        """
        Calls a tool safely.

        Some tools currently do not accept policy_reasoning.
        Later, when tools.py is updated, tools can accept policy_reasoning directly.

        This helper keeps the executor compatible with both:
        - old tool signatures
        - new policy-reasoning-aware tool signatures
        """

        signature = inspect.signature(tool_function)

        if "policy_reasoning" in signature.parameters:
            kwargs["policy_reasoning"] = policy_reasoning

        return tool_function(**kwargs)

    def _extract_policy_refs_from_reasoning(
        self,
        policy_reasoning: Optional[PolicyReasoningResponse],
    ) -> List[str]:
        """
        Extracts policy IDs selected by Gemini during policy reasoning.
        These are added to policy_references so the final Gemini validator
        can see both LLM-selected policies and Python tool references.
        """

        if policy_reasoning is None:
            return []

        policy_refs: List[str] = []

        for match in policy_reasoning.expense_policy_matches:
            policy_refs.extend(match.applicable_policy_ids)

        policy_refs.extend(policy_reasoning.claim_level_policy_ids)

        return self._unique(policy_refs)

    def _extract_manual_review_from_reasoning(
        self,
        policy_reasoning: Optional[PolicyReasoningResponse],
    ) -> List[str]:
        """
        Converts Gemini policy reasoning risks into manual review signals.

        If Gemini identifies missing/conflicting policy information or marks
        an expense for manual review, this information is carried forward.
        """

        if policy_reasoning is None:
            return []

        manual_review_reasons: List[str] = []

        manual_review_reasons.extend(
            policy_reasoning.missing_or_conflicting_info
        )

        for match in policy_reasoning.expense_policy_matches:
            if match.manual_review_required:
                manual_review_reasons.append(
                    f"{match.expense_id}: Gemini policy reasoning marked this "
                    "expense for Manual Review."
                )

            for flag in match.risk_flags:
                if flag:
                    manual_review_reasons.append(
                        f"{match.expense_id}: policy reasoning risk flag - {flag}"
                    )

        return self._unique(manual_review_reasons)

    def _model_to_dict(self, value: Any) -> Any:
        if value is None:
            return {}

        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")

        return value

    def _unique(self, items: List[str]) -> List[str]:
        seen = set()
        output = []

        for item in items:
            if item and item not in seen:
                seen.add(item)
                output.append(item)

        return output