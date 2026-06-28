import argparse
import json
from pathlib import Path

from app.agent import TravelReimbursementAgent
from app.policy_loader import PolicyDataLoader


def run_demo(claim_id: str, use_llm: bool) -> None:
    loader = PolicyDataLoader()
    data = loader.load_all()

    agent = TravelReimbursementAgent(
        policy_text=data["policy_text"],
        receipt_map=data["receipt_map"],
        historical_claims=data["claims"],
        approval_matrix=data["approval_matrix"],
        expense_limits=data["expense_limits"],
        use_llm=use_llm,
    )

    claim = loader.get_claim_by_id(claim_id)

    print("=" * 80)
    print("Travel Reimbursement Approval Agent Demo")
    print("=" * 80)
    print(f"Evaluating claim: {claim.claim_id} - {claim.employee_name}")
    print(f"LLM enabled:      {use_llm}")
    print("-" * 80)

    result = agent.evaluate_claim(claim)

    planning_status = "WORKING" if agent.last_planning_llm_used else "FALLBACK USED"
    policy_status = (
        "WORKING" if agent.last_policy_reasoning_llm_used else "FALLBACK USED"
    )
    final_status = "WORKING" if agent.last_final_llm_used else "FALLBACK USED"

    print(f"Gemini planning:         {planning_status}")
    print(f"Gemini policy reasoning: {policy_status}")
    print(f"Gemini final response:   {final_status}")
    print("-" * 80)
    print(f"Decision:                {result.decision}")
    print(f"Submitted amount:        INR {result.submitted_amount}")
    print(f"Approved amount:         INR {result.approved_amount}")
    print(f"Rejected amount:         INR {result.rejected_amount}")
    print(f"Approval level:          {result.approval_level}")
    print(f"Reason codes:            {result.reason_codes}")
    print(f"Manual review reasons:   {result.manual_review_reasons}")
    print("-" * 80)
    print("Final recommendation:")
    print(result.explanation)

    output_dir = Path("sample_outputs")
    output_dir.mkdir(exist_ok=True)

    output_path = output_dir / f"{claim.claim_id.lower()}_demo_result.json"

    result_record = {
        "gemini_status": {
            "planning_llm": planning_status,
            "policy_reasoning_llm": policy_status,
            "final_llm": final_status,
        },
        "result": result.model_dump(mode="json"),
    }

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(result_record, file, indent=2, ensure_ascii=False)

    print("=" * 80)
    print(f"Demo result saved to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a demo evaluation for the Travel Reimbursement Approval Agent."
    )

    parser.add_argument(
        "--claim-id",
        default="CLM-006",
        help="Sample claim ID to evaluate. Default: CLM-006",
    )

    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Run demo without Gemini calls.",
    )

    args = parser.parse_args()

    run_demo(
        claim_id=args.claim_id,
        use_llm=not args.no_llm,
    )