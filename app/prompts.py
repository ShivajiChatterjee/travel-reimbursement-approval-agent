import json
from typing import Any, Dict

from app.models import TravelClaim


AVAILABLE_TOOLS = [
    {
        "tool_name": "policy_lookup_tool",
        "description": "Retrieves relevant reimbursement policy sections for the claim.",
    },
    {
        "tool_name": "travel_type_validation_tool",
        "description": "Validates whether domestic/international travel type matches origin and destination countries.",
    },
    {
        "tool_name": "receipt_completeness_tool",
        "description": "Checks whether required receipts exist, have attachments, and match expense details.",
    },
    {
        "tool_name": "expense_eligibility_tool",
        "description": "Checks whether expense categories are eligible, non-reimbursable, or unknown.",
    },
    {
        "tool_name": "duplicate_claim_detector_tool",
        "description": "Detects duplicate expenses against historical submitted claims.",
    },
    {
        "tool_name": "limit_checker_tool",
        "description": "Calculates approved and rejected amounts based on travel type, expense category, limits, duplicates, and missing fields.",
    },
    {
        "tool_name": "approval_threshold_tool",
        "description": "Determines approval routing level based on calculated claim amount.",
    },
]


TRAVEL_POLICY_INDEX = [
    {
        "policy_id": "POL-001",
        "title": "Travel eligibility and travel type",
        "description": "Defines valid business travel claims and domestic/international travel classification.",
    },
    {
        "policy_id": "POL-002",
        "title": "Receipt requirement",
        "description": "Defines receipt and attachment requirements, especially for expenses above INR 500.",
    },
    {
        "policy_id": "POL-003",
        "title": "Eligible expense categories",
        "description": "Defines reimbursable categories such as flight, train, hotel, meals, taxi, and conference.",
    },
    {
        "policy_id": "POL-004",
        "title": "Non-reimbursable expense categories",
        "description": "Defines blocked categories such as alcohol, personal shopping, entertainment, and unrelated personal expenses.",
    },
    {
        "policy_id": "POL-005",
        "title": "Domestic travel limits",
        "description": "Defines domestic limits for hotel, meals, taxi, and other applicable categories.",
    },
    {
        "policy_id": "POL-006",
        "title": "International travel limits",
        "description": "Defines international limits for hotel, meals, taxi, and other applicable categories.",
    },
    {
        "policy_id": "POL-007",
        "title": "Flight class and prior approval",
        "description": "Defines economy-only flight reimbursement and prior approval requirements for non-economy travel.",
    },
    {
        "policy_id": "POL-008",
        "title": "Duplicate claim rule",
        "description": "Defines duplicate expense detection and duplicate reimbursement restrictions.",
    },
    {
        "policy_id": "POL-009",
        "title": "Approval thresholds",
        "description": "Defines manager, finance, and director approval routing based on approved amount.",
    },
    {
        "policy_id": "POL-010",
        "title": "Decision rules",
        "description": "Defines approve, partially approve, reject, and manual review decision rules.",
    },
    {
        "policy_id": "POL-011",
        "title": "Approved and rejected amount calculation",
        "description": "Defines how approved and rejected amounts should be calculated.",
    },
    {
        "policy_id": "POL-012",
        "title": "Confidence and explanation",
        "description": "Defines confidence and explanation expectations for the final recommendation.",
    },
]


TOOL_PLANNING_SCHEMA = {
    "selected_tools": [
        {
            "tool_name": "string",
            "reason": "string explaining why this tool is required",
            "required": "boolean",
        }
    ],
    "missing_or_conflicting_info": ["string"],
    "planning_summary": "short summary of why these tools are needed",
}


POLICY_REASONING_SCHEMA = {
    "claim_id": "string",
    "expense_policy_matches": [
        {
            "expense_id": "string",
            "category": "string",
            "applicable_policy_ids": ["POL-001", "POL-002"],
            "policy_reasoning": "string explaining why these policies apply to this expense",
            "decision_signal": (
                "eligible | non_reimbursable | limit_check_required | "
                "receipt_check_required | duplicate_check_required | "
                "manual_review | unclear"
            ),
            "requires_calculation": "boolean",
            "manual_review_required": "boolean",
            "risk_flags": ["string"],
            "confidence": "number between 0 and 1",
        }
    ],
    "claim_level_policy_ids": ["POL-009", "POL-010", "POL-011"],
    "missing_or_conflicting_info": ["string"],
    "overall_reasoning": "string summary of the policy reasoning for the claim",
    "confidence": "number between 0 and 1",
}


FINAL_JSON_SCHEMA = {
    "claim_id": "string",
    "employee_id": "string",
    "employee_name": "string",
    "decision": "Approve | Partially Approve | Reject | Manual Review",
    "submitted_amount": "number",
    "approved_amount": "number",
    "rejected_amount": "number",
    "approval_level": "manager | finance | director | unknown",
    "missing_documents": ["string"],
    "manual_review_reasons": ["string"],
    "policy_references": ["string"],
    "confidence": "number between 0 and 1",
    "explanation": (
        "detailed business explanation referencing each expense by expense_id, "
        "approved and rejected amounts per expense line, policy rule IDs such as "
        "POL-005 and POL-007, and the approval routing level"
    ),
    "reason_codes": ["string"],
    "expense_decisions": [
        {
            "expense_id": "string",
            "category": "string",
            "submitted_amount": "number",
            "approved_amount": "number",
            "rejected_amount": "number",
            "status": "approved | partially_approved | rejected | manual_review",
            "reason": "string",
            "policy_references": ["string"],
        }
    ],
}


DECISION_RULES = """
Decision consistency rules:

1. Return "Manual Review" if:
   - manual_review_reasons is not empty, OR
   - missing_documents is not empty, OR
   - any expense_decision status is "manual_review", OR
   - approval threshold tool says manual_review_required is true.

2. Return "Reject" if:
   - there is no manual review issue, AND
   - approved_amount is 0, AND
   - rejected_amount is greater than 0.

3. Return "Partially Approve" if:
   - there is no manual review issue, AND
   - approved_amount is greater than 0, AND
   - rejected_amount is greater than 0.

4. Return "Approve" if:
   - there is no manual review issue, AND
   - approved_amount equals submitted_amount, AND
   - rejected_amount is 0.

5. Do not invent missing documents, amounts, policy IDs, or reason codes.
6. Do not change submitted_amount, approved_amount, rejected_amount, or approval_level.
7. Use the LLM policy reasoning and deterministic tool results together.
8. Deterministic tool results are the source of truth for numeric calculations.
"""


STRICT_OUTPUT_RULES = """
Output rules:

- Return only valid JSON.
- Do not include markdown.
- Do not include code fences.
- Do not include commentary before or after the JSON.
- Use double quotes for all JSON keys and string values.
- The decision value must be exactly one of:
  "Approve", "Partially Approve", "Reject", "Manual Review".
- The confidence value must be a number between 0 and 1.
- The explanation must be detailed and business-friendly.
- The explanation must be 2 to 5 sentences.
- The explanation must reference relevant expense lines by expense_id and category.
- The explanation must mention approved and rejected amounts for relevant expense lines.
- The explanation must cite relevant policy rule IDs such as POL-005, POL-007, POL-009 when those policy IDs are present in the policy reasoning or tool results.
- The explanation must mention the approval routing level when approval routing is required.
- policy_references must only contain policy IDs present in the policy reasoning or tool results.
- reason_codes must only contain reason codes present in the tool results.
- expense_decisions must match the calculated tool results.
"""


def _to_jsonable(value: Any) -> Any:
    """
    Converts Pydantic models and nested Python objects into JSON-safe objects.
    """

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]

    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]

    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(
        _to_jsonable(value),
        indent=2,
        ensure_ascii=False,
    )


def build_tool_planning_prompt(claim: TravelClaim) -> str:
    """
    Builds the first Gemini prompt.

    Purpose:
    Gemini reviews the claim and decides which tools are required.
    Gemini must not make the final reimbursement decision in this step.
    """

    claim_json = _json_dumps(claim)
    tools_json = _json_dumps(AVAILABLE_TOOLS)
    schema_json = _json_dumps(TOOL_PLANNING_SCHEMA)

    prompt = f"""
You are the planning module of a Travel Reimbursement Approval Agent.

Your job in this step is ONLY to decide which tools are required to evaluate the submitted travel reimbursement claim.

Do not approve, reject, partially approve, or manually review the claim in this step.
Do not calculate final approved or rejected amounts in this step.
Do not produce the final reimbursement decision in this step.

You have access to these tools:

{tools_json}

Submitted claim:

{claim_json}

Tool selection rules:

1. Select policy_lookup_tool for every claim because policy context is always required.

2. Select travel_type_validation_tool when travel_type, origin_country, or destination_country is present or needed.

3. Select receipt_completeness_tool when:
   - any expense amount is greater than INR 500, OR
   - any expense contains receipt_id, OR
   - receipt evidence may affect approval/manual review.

4. Select expense_eligibility_tool for every claim because every category must be checked.

5. Select duplicate_claim_detector_tool when historical claims are available or duplicate risk must be checked.

6. Select limit_checker_tool for every claim because approved and rejected amounts must be calculated.

7. Select approval_threshold_tool for every claim because routing level must be determined.

8. For this prototype, you should normally select all seven tools unless a tool is clearly impossible to run.

9. Do not select only one tool and stop. A valid reimbursement evaluation usually requires multiple tools.

10. If information is missing or conflicting, include that in missing_or_conflicting_info, but still select the tools needed to detect and handle the issue.

Return only valid JSON matching this schema:

{schema_json}

Strict output rules:

- Return only JSON.
- Do not include markdown.
- Do not include code fences.
- Do not include commentary before or after JSON.
- selected_tools must contain tool names exactly as listed in available tools.
- Every selected tool must include a clear reason.
""".strip()

    return prompt


def build_policy_reasoning_prompt(
    claim: TravelClaim,
    policy_text: str,
) -> str:
    """
    Builds the second Gemini prompt.

    Purpose:
    Gemini reads the submitted claim and travel policy document, then decides
    which policy rules apply to each expense.

    Gemini must not calculate final amounts here.
    Python will calculate exact approved/rejected amounts later.
    """

    claim_json = _json_dumps(claim)
    policy_index_json = _json_dumps(TRAVEL_POLICY_INDEX)
    schema_json = _json_dumps(POLICY_REASONING_SCHEMA)

    prompt = f"""
You are the policy reasoning module of a Travel Reimbursement Approval Agent.

Your job in this step is to read:
1. The submitted claim.
2. The travel reimbursement policy document.
3. The available policy ID index.

Then decide which policy IDs apply to each expense line.

This is the claim-to-policy reasoning step.
You should act like a finance policy reviewer who maps every expense to the relevant company travel policy rules.

IMPORTANT:
- Do not calculate final approved amount.
- Do not calculate final rejected amount.
- Do not perform arithmetic.
- Do not decide approval routing level such as manager, finance, or director.
- Do not make the final reimbursement decision.
- Only identify applicable policies, reasoning signals, missing/conflicting information, and manual review risk.
- If POL-009 applies, only state that approval threshold policy applies. Do not decide the threshold level.
- Python tools will perform exact calculations, approval routing, and validation after this step.

AVAILABLE POLICY ID INDEX:

{policy_index_json}

TRAVEL REIMBURSEMENT POLICY DOCUMENT:

{policy_text}

SUBMITTED CLAIM:

{claim_json}

Policy reasoning instructions:

1. Include every expense from the claim in expense_policy_matches.

2. For each expense, identify all applicable policy IDs from the available policy index.

3. Choose policy IDs based on the claim fields:
   - travel_type
   - origin_country
   - destination_country
   - expense category
   - expense amount
   - receipt_id
   - travel_class
   - number_of_nights
   - business purpose

4. Use POL-002 when receipt requirement may apply.

5. Use POL-003 when the expense category appears reimbursable.

6. Use POL-004 when the expense category appears non-reimbursable.

7. Use POL-005 when domestic travel limits may apply.

8. Use POL-006 when international travel limits may apply.

9. Use POL-007 when flight class or prior approval may apply.

10. Use POL-008 when duplicate claim checking may apply.

11. Use POL-009, POL-010, and POL-011 as claim-level policies when approval routing, decision rules, or amount calculation are relevant. Do not decide the approval routing level in this step; Python will calculate the routing level later.

12. If the policy document does not clearly support a policy match, mark the expense as unclear or manual_review.

13. Do not invent policy IDs. Use only policy IDs from the available policy index.

14. If the claim data conflicts with the selected policy, include the conflict in missing_or_conflicting_info.

15. decision_signal must be one of:
    eligible,
    non_reimbursable,
    limit_check_required,
    receipt_check_required,
    duplicate_check_required,
    manual_review,
    unclear.

16. requires_calculation should normally be true for reimbursable or potentially reimbursable expenses because Python still needs to calculate submitted, approved, and rejected amounts.

17. Do not add missing_or_conflicting_info unless there is a real missing field, contradiction, invalid travel type, missing receipt reference, missing travel class, missing number_of_nights, unclear category, or policy conflict.

18. Do not mark business purpose as unclear if it contains a reasonable business explanation.

Return only valid JSON matching this schema:

{schema_json}

Strict output rules:

- Return only JSON.
- Do not include markdown.
- Do not include code fences.
- Do not include commentary before or after JSON.
- Use double quotes for all keys and string values.
- claim_id must match the submitted claim_id.
- Every expense_id from the claim must appear exactly once in expense_policy_matches.
- applicable_policy_ids must only contain valid policy IDs from the available policy index.
- confidence must be a number between 0 and 1.
""".strip()

    return prompt


def build_reimbursement_prompt(
    claim: TravelClaim,
    tool_output: Dict[str, Any],
) -> str:
    """
    Builds the final Gemini prompt.

    Gemini uses:
    - submitted claim
    - Gemini policy reasoning
    - deterministic Python tool results

    Gemini can recommend the final decision, but it must not recalculate amounts.
    Numeric calculations come from Python tools.
    """

    claim_json = _json_dumps(claim)

    policy_context = {}

    for tool in tool_output.get("tools_called", []):
        if getattr(tool, "tool_name", None) == "policy_lookup_tool":
            policy_context = tool.details.get("policy_context", {})
            break

        if isinstance(tool, dict) and tool.get("tool_name") == "policy_lookup_tool":
            policy_context = tool.get("details", {}).get("policy_context", {})
            break

    grounded_evidence = {
        "llm_tool_plan": tool_output.get("llm_tool_plan", {}),
        "validated_tool_plan": tool_output.get("validated_tool_plan", {}),
        "llm_policy_reasoning": tool_output.get("policy_reasoning", {}),
        "policy_context": policy_context,
        "tool_plan": tool_output.get("tool_plan", {}),
        "submitted_amount": tool_output.get("submitted_amount", 0),
        "approved_amount": tool_output.get("approved_amount", 0),
        "rejected_amount": tool_output.get("rejected_amount", 0),
        "amount_for_approval": tool_output.get("amount_for_approval", 0),
        "approval_level": tool_output.get("approval_level", "unknown"),
        "missing_documents": tool_output.get("missing_documents", []),
        "manual_review_reasons": tool_output.get("manual_review_reasons", []),
        "policy_references": tool_output.get("policy_references", []),
        "reason_codes": tool_output.get("reason_codes", []),
        "expense_decisions": tool_output.get("expense_decisions", []),
        "tools_called": tool_output.get("tools_called", []),
    }

    evidence_json = _json_dumps(grounded_evidence)
    schema_json = _json_dumps(FINAL_JSON_SCHEMA)

    prompt = f"""
You are a Travel Reimbursement Approval Agent.

Your job is to produce the final reimbursement recommendation using:
1. The submitted claim JSON.
2. The LLM policy reasoning result.
3. The deterministic Python tool results.
4. The policy references and policy context returned by the tools.
5. The LLM tool-planning result and the validated tool execution plan.

The LLM policy reasoning step identified which policy rules apply to each expense.
The Python tools performed deterministic checks and numeric calculations.
Now validate whether the policy reasoning and Python tool results are consistent with each other.
Then produce the final structured reimbursement recommendation.

IMPORTANT:
- You should semantically validate whether the selected policies, expense decisions, reason codes, and calculated amounts are consistent.
- If the policy reasoning and tool results conflict, prefer the deterministic tool results for amounts and clearly mention the inconsistency as a manual review reason if needed.
- You are not allowed to invent facts.
- You are not allowed to recalculate amounts differently.
- You are not allowed to ignore tool results.
- Deterministic Python tool results are the source of truth for:
  submitted_amount,
  approved_amount,
  rejected_amount,
  missing_documents,
  manual_review_reasons,
  reason_codes,
  and expense-level calculated amounts.

CLAIM JSON:
{claim_json}

POLICY REASONING AND TOOL EVIDENCE:
{evidence_json}

FINAL JSON SCHEMA:
{schema_json}

{DECISION_RULES}

{STRICT_OUTPUT_RULES}

Now return the final reimbursement decision as valid JSON only.
""".strip()

    return prompt


if __name__ == "__main__":
    from app.policy_loader import PolicyDataLoader
    from app.tools import run_reimbursement_tools

    loader = PolicyDataLoader()
    data = loader.load_all()

    sample_claim = loader.get_claim_by_id("CLM-006")

    tool_planning_prompt = build_tool_planning_prompt(sample_claim)

    policy_reasoning_prompt = build_policy_reasoning_prompt(
        claim=sample_claim,
        policy_text=data["policy_text"],
    )

    tool_output = run_reimbursement_tools(
        claim=sample_claim,
        policy_text=data["policy_text"],
        receipt_map=data["receipt_map"],
        historical_claims=data["claims"],
        approval_matrix=data["approval_matrix"],
        expense_limits=data["expense_limits"],
    )

    final_prompt = build_reimbursement_prompt(
        claim=sample_claim,
        tool_output=tool_output,
    )

    print("Prompt test successful")
    print("=" * 80)
    print("TOOL PLANNING PROMPT PREVIEW")
    print("=" * 80)
    print(tool_planning_prompt[:2000])
    print("=" * 80)
    print("Tool planning prompt length:", len(tool_planning_prompt), "characters")

    print("=" * 80)
    print("POLICY REASONING PROMPT PREVIEW")
    print("=" * 80)
    print(policy_reasoning_prompt[:3000])
    print("=" * 80)
    print("Policy reasoning prompt length:", len(policy_reasoning_prompt), "characters")

    print("=" * 80)
    print("FINAL REIMBURSEMENT PROMPT PREVIEW")
    print("=" * 80)
    print(final_prompt[:3000])
    print("=" * 80)
    print("Final prompt length:", len(final_prompt), "characters")