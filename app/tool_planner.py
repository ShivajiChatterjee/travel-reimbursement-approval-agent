from typing import Any, Dict, List

from app.llm_client import GeminiClient
from app.models import LLMToolPlan, PlannedToolCall, TravelClaim
from app.prompts import build_tool_planning_prompt
from app.tools import build_tool_plan


VALID_TOOL_NAMES = {
    "policy_lookup_tool",
    "travel_type_validation_tool",
    "receipt_completeness_tool",
    "expense_eligibility_tool",
    "duplicate_claim_detector_tool",
    "limit_checker_tool",
    "approval_threshold_tool",
}


TOOL_EXECUTION_ORDER = [
    "policy_lookup_tool",
    "travel_type_validation_tool",
    "receipt_completeness_tool",
    "expense_eligibility_tool",
    "duplicate_claim_detector_tool",
    "limit_checker_tool",
    "approval_threshold_tool",
]


class ToolPlanner:
    """
    Handles LLM-driven tool planning and Python safety validation.

    Gemini proposes the tools.
    Python validates the plan and adds mandatory tools if Gemini skips any.
    """

    def __init__(
        self,
        historical_claims: List[TravelClaim],
        llm_client: GeminiClient,
    ):
        self.historical_claims = historical_claims
        self.llm_client = llm_client

        self.last_planning_llm_used = False
        self.last_raw_planning_response = ""

        self.last_llm_tool_plan: Dict[str, Any] = {}
        self.last_validated_tool_plan: Dict[str, Any] = {}

    def get_llm_tool_plan(self, claim: TravelClaim) -> LLMToolPlan:
        """
        First LLM call.

        Gemini decides which tools are required.
        If Gemini fails, deterministic planning fallback is used.
        """

        self.last_planning_llm_used = False
        self.last_raw_planning_response = ""

        if self.llm_client.is_available:
            try:
                planning_prompt = build_tool_planning_prompt(claim)
                raw_response = self.llm_client.call_json(planning_prompt)

                self.last_raw_planning_response = raw_response
                parsed = self.llm_client.extract_json(raw_response)

                plan = LLMToolPlan.model_validate(parsed)

                self.last_planning_llm_used = True
                self.last_llm_tool_plan = plan.model_dump(mode="json")

                return plan

            except Exception as error:
                print(
                    "Gemini tool planning failed. "
                    f"Using deterministic tool plan. Error: {error}"
                )

        fallback_plan = self._build_deterministic_tool_plan_as_llm_plan(claim)
        self.last_llm_tool_plan = fallback_plan.model_dump(mode="json")

        return fallback_plan

    def _build_deterministic_tool_plan_as_llm_plan(
        self,
        claim: TravelClaim,
    ) -> LLMToolPlan:
        """
        Fallback planning if Gemini planning fails.
        """

        safety_plan = build_tool_plan(
            claim=claim,
            historical_claims=self.historical_claims,
        )

        planned_tools = []

        for tool_name in safety_plan.get("selected_tools", []):
            planned_tools.append(
                PlannedToolCall(
                    tool_name=tool_name,
                    reason=(
                        "Selected by deterministic fallback planning because "
                        "this check is required for reimbursement evaluation."
                    ),
                    required=True,
                )
            )

        return LLMToolPlan(
            selected_tools=planned_tools,
            missing_or_conflicting_info=[],
            planning_summary=(
                "Deterministic fallback selected the required reimbursement tools."
            ),
        )

    def validate_llm_tool_plan(
        self,
        claim: TravelClaim,
        llm_tool_plan: LLMToolPlan,
    ) -> Dict[str, Any]:
        """
        Validates Gemini's tool plan.

        Gemini is allowed to decide tools, but Python enforces mandatory
        reimbursement controls so required checks cannot be skipped.
        """

        llm_selected_tools = []

        for planned_tool in llm_tool_plan.selected_tools:
            if planned_tool.tool_name in VALID_TOOL_NAMES:
                llm_selected_tools.append(planned_tool.tool_name)

        safety_plan = build_tool_plan(
            claim=claim,
            historical_claims=self.historical_claims,
        )

        mandatory_tools = safety_plan.get("selected_tools", [])

        final_tool_set = set(llm_selected_tools).union(set(mandatory_tools))

        final_execution_order = [
            tool_name
            for tool_name in TOOL_EXECUTION_ORDER
            if tool_name in final_tool_set
        ]

        skipped_by_llm_added_by_python = [
            tool_name
            for tool_name in final_execution_order
            if tool_name not in llm_selected_tools
        ]

        validated_tool_plan = {
            "llm_selected_tools": llm_selected_tools,
            "mandatory_tools": mandatory_tools,
            "final_execution_order": final_execution_order,
            "skipped_by_llm_added_by_python": skipped_by_llm_added_by_python,
            "missing_or_conflicting_info": llm_tool_plan.missing_or_conflicting_info,
            "planning_summary": llm_tool_plan.planning_summary,
            "safety_note": (
                "Gemini proposed the tool plan. Python validated the plan and "
                "added any mandatory reimbursement controls that Gemini skipped."
            ),
        }

        self.last_validated_tool_plan = validated_tool_plan
        return validated_tool_plan