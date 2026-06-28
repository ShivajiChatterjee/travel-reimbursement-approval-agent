import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.llm_client import GeminiClient
from app.models import EvaluationResponse, Receipt, TravelClaim
from app.policy_loader import PolicyDataLoader
from app.policy_reasoner import PolicyReasoner
from app.prompts import build_reimbursement_prompt
from app.response_builder import ResponseBuilder
from app.tool_executor import ToolExecutor
from app.tool_planner import ToolPlanner


class TravelReimbursementAgent:
    """
    Main orchestration layer for the Travel Reimbursement Approval Agent.

    Flow:
    1. Gemini plans which tools are required.
    2. Python validates Gemini's tool plan.
    3. Gemini reads the policy and maps each expense to policy IDs.
    4. Python executes deterministic tools using the policy reasoning.
    5. Gemini receives tool outputs and semantically validates the result.
    6. Python repairs critical fields from deterministic tool truth.
    7. Pydantic validates the final EvaluationResponse.
    """

    def __init__(
        self,
        policy_text: str,
        receipt_map: Dict[str, Receipt],
        historical_claims: List[TravelClaim],
        approval_matrix: Any,
        expense_limits: Any,
        use_llm: bool = True,
        model_name: Optional[str] = None,
    ):
        self.policy_text = policy_text
        self.receipt_map = receipt_map
        self.historical_claims = historical_claims
        self.approval_matrix = approval_matrix
        self.expense_limits = expense_limits
        self.use_llm = use_llm

        self.llm_client = GeminiClient(
            use_llm=use_llm,
            model_name=model_name,
        )

        self.tool_planner = ToolPlanner(
            historical_claims=historical_claims,
            llm_client=self.llm_client,
        )

        self.policy_reasoner = PolicyReasoner(
            policy_text=policy_text,
            llm_client=self.llm_client,
            use_llm=use_llm,
        )

        self.tool_executor = ToolExecutor(
            policy_text=policy_text,
            receipt_map=receipt_map,
            historical_claims=historical_claims,
            approval_matrix=approval_matrix,
            expense_limits=expense_limits,
        )

        self.response_builder = ResponseBuilder()

        # Debug/demo state for screenshots, audit trail, and JSON output.
        self.last_llm_used = False

        self.last_planning_llm_used = False
        self.last_policy_reasoning_llm_used = False
        self.last_final_llm_used = False

        self.last_raw_planning_response = ""
        self.last_raw_policy_reasoning_response = ""
        self.last_raw_final_response = ""

        self.last_llm_tool_plan: Dict[str, Any] = {}
        self.last_validated_tool_plan: Dict[str, Any] = {}
        self.last_policy_reasoning: Dict[str, Any] = {}
        self.last_policy_reasoning_error = ""

    def evaluate_claim(self, claim: TravelClaim) -> EvaluationResponse:
        """
        Runs the complete agentic workflow.

        LLM tool planning
        -> Python plan validation
        -> LLM policy reasoning
        -> Python deterministic tool execution
        -> LLM final semantic validation
        -> Python hard repair
        -> Pydantic validation
        """

        # Step 1: Gemini plans which tools are needed.
        llm_tool_plan = self.tool_planner.get_llm_tool_plan(claim)

        # Step 2: Python validates and repairs the tool plan.
        validated_tool_plan = self.tool_planner.validate_llm_tool_plan(
            claim=claim,
            llm_tool_plan=llm_tool_plan,
        )

        self._sync_planning_state()

        # Step 3: Gemini maps claim expenses to applicable policy IDs.
        policy_reasoning = self.policy_reasoner.reason_over_policy(claim)
        self._sync_policy_reasoning_state()

        # Step 4: Python executes deterministic tools using policy reasoning.
        tool_output = self.tool_executor.execute(
            claim=claim,
            llm_tool_plan=llm_tool_plan,
            validated_tool_plan=validated_tool_plan,
            policy_reasoning=policy_reasoning,
        )

        # Step 5: Gemini validates the evidence and recommends final decision.
        prompt = build_reimbursement_prompt(
            claim=claim,
            tool_output=tool_output,
        )

        llm_data = None
        self.last_final_llm_used = False
        self.last_raw_final_response = ""

        if self.llm_client.is_available:
            try:
                raw_response = self.llm_client.call_json(prompt)
                self.last_raw_final_response = raw_response
                llm_data = self.llm_client.extract_json(raw_response)
                self.last_final_llm_used = True

            except Exception as error:
                print(
                    "Final Gemini call failed. "
                    f"Using deterministic fallback. Error: {error}"
                )

        # Step 6: If final Gemini fails, build deterministic fallback.
        if llm_data is None:
            llm_data = self.response_builder.build_fallback_response(
                claim=claim,
                tool_output=tool_output,
            )

        # Step 7: Python hard guardrail repairs critical fields.
        repaired_data = self.response_builder.repair_with_tool_truth(
            llm_data=llm_data,
            claim=claim,
            tool_output=tool_output,
        )

        self.last_llm_used = (
            self.last_planning_llm_used
            or self.last_policy_reasoning_llm_used
            or self.last_final_llm_used
        )

        # Step 8: Validate final response with Pydantic.
        try:
            return EvaluationResponse.model_validate(repaired_data)

        except ValidationError as error:
            print("Pydantic validation failed. Using deterministic fallback response.")
            print(error)

            fallback_data = self.response_builder.build_fallback_response(
                claim=claim,
                tool_output=tool_output,
            )

            repaired_fallback = self.response_builder.repair_with_tool_truth(
                llm_data=fallback_data,
                claim=claim,
                tool_output=tool_output,
            )

            return EvaluationResponse.model_validate(repaired_fallback)

    def _sync_planning_state(self) -> None:
        """
        Copies planner debug state into the main agent.
        """

        self.last_planning_llm_used = getattr(
            self.tool_planner,
            "last_planning_llm_used",
            False,
        )

        self.last_raw_planning_response = getattr(
            self.tool_planner,
            "last_raw_planning_response",
            "",
        )

        self.last_llm_tool_plan = self._to_jsonable(
            getattr(
                self.tool_planner,
                "last_llm_tool_plan",
                {},
            )
        )

        self.last_validated_tool_plan = self._to_jsonable(
            getattr(
                self.tool_planner,
                "last_validated_tool_plan",
                {},
            )
        )

    def _sync_policy_reasoning_state(self) -> None:
        """
        Copies policy reasoning debug state into the main agent.

        Uses getattr so the agent does not crash if PolicyReasoner does not
        expose every optional debug attribute.
        """

        self.last_policy_reasoning_llm_used = getattr(
            self.policy_reasoner,
            "last_policy_reasoning_llm_used",
            False,
        )

        self.last_raw_policy_reasoning_response = getattr(
            self.policy_reasoner,
            "last_raw_policy_reasoning_response",
            "",
        )

        self.last_policy_reasoning = self._to_jsonable(
            getattr(
                self.policy_reasoner,
                "last_policy_reasoning",
                {},
            )
        )

        self.last_policy_reasoning_error = getattr(
            self.policy_reasoner,
            "last_policy_reasoning_error",
            "",
        )

    def _to_jsonable(self, value: Any) -> Any:
        """
        Converts Pydantic models and nested objects into JSON-safe data.
        """

        if value is None:
            return {}

        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")

        if isinstance(value, dict):
            return {
                key: self._to_jsonable(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [
                self._to_jsonable(item)
                for item in value
            ]

        if isinstance(value, tuple):
            return [
                self._to_jsonable(item)
                for item in value
            ]

        return value


if __name__ == "__main__":
    loader = PolicyDataLoader()
    data = loader.load_all()

    agent = TravelReimbursementAgent(
        policy_text=data["policy_text"],
        receipt_map=data["receipt_map"],
        historical_claims=data["claims"],
        approval_matrix=data["approval_matrix"],
        expense_limits=data["expense_limits"],
        use_llm=True,
    )

    output_dir = Path("sample_outputs")
    output_dir.mkdir(exist_ok=True)

    json_output_path = output_dir / "clm_006_result.json"

    sample_claim = loader.get_claim_by_id("CLM-006")

    print("Checking CLM-006 with Gemini...")
    print("=" * 80)

    result = agent.evaluate_claim(sample_claim)

    planning_status = (
        "WORKING"
        if agent.last_planning_llm_used
        else "FAILED - deterministic fallback used"
    )

    policy_reasoning_status = (
        "WORKING"
        if agent.last_policy_reasoning_llm_used
        else "FAILED - safe policy fallback used"
    )

    final_status = (
        "WORKING"
        if agent.last_final_llm_used
        else "FAILED - deterministic fallback used"
    )

    overall_status = "WORKING" if agent.last_llm_used else "NOT USED"

    print(f"Claim checked:              {result.claim_id} - {result.employee_name}")
    print(f"Gemini planning LLM:        {planning_status}")
    print(f"Gemini policy reasoning:    {policy_reasoning_status}")
    print(f"Gemini final LLM:           {final_status}")
    print(f"Overall Gemini used:        {overall_status}")
    print("-" * 80)
    print(f"Decision:                   {result.decision}")
    print(f"Submitted amount:           {result.submitted_amount}")
    print(f"Approved amount:            {result.approved_amount}")
    print(f"Rejected amount:            {result.rejected_amount}")
    print(f"Approval level:             {result.approval_level}")
    print(f"Reason codes:               {result.reason_codes}")
    print(f"Manual review:              {result.manual_review_reasons}")
    print(f"Explanation:                {result.explanation}")

    result_record = {
        "planning_llm_used": agent.last_planning_llm_used,
        "policy_reasoning_llm_used": agent.last_policy_reasoning_llm_used,
        "final_llm_used": agent.last_final_llm_used,
        "llm_used": agent.last_llm_used,

        "gemini_status": {
            "planning_llm": planning_status,
            "policy_reasoning_llm": policy_reasoning_status,
            "final_llm": final_status,
            "overall": overall_status,
        },

        "llm_responses": {
            "raw_planning_response": agent.last_raw_planning_response,
            "raw_policy_reasoning_response": agent.last_raw_policy_reasoning_response,
            "raw_final_response": agent.last_raw_final_response,
        },

        "llm_policy_reasoning": agent.last_policy_reasoning,
        "policy_reasoning_error": agent.last_policy_reasoning_error,

        "ui_display": {
            "ai_recommendation": result.explanation,
            "llm_planning_summary": result.tool_plan.get("planning_summary", ""),
            "policy_reasoning_summary": agent.last_policy_reasoning.get(
                "overall_reasoning",
                "",
            ),
            "decision": result.decision,
            "approval_level": result.approval_level,
            "approved_amount": result.approved_amount,
            "rejected_amount": result.rejected_amount,
        },

        "tools_selected_by_llm": agent.last_validated_tool_plan.get(
            "llm_selected_tools",
            [],
        ),
        "tools_executed": agent.last_validated_tool_plan.get(
            "final_execution_order",
            [],
        ),
        "safety_added_tools": agent.last_validated_tool_plan.get(
            "skipped_by_llm_added_by_python",
            [],
        ),

        **result.model_dump(mode="json"),
    }

    with open(json_output_path, "w", encoding="utf-8") as file:
        json.dump(result_record, file, indent=2, ensure_ascii=False)

    print("=" * 80)
    print(f"JSON output saved to: {json_output_path}")