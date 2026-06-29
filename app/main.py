import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from app.agent import TravelReimbursementAgent
from app.models import TravelClaim
from app.policy_loader import PolicyDataLoader


app = FastAPI(
    title="Travel Reimbursement Approval Agent",
    description=(
        "A GenAI-powered travel reimbursement approval API using Gemini for "
        "tool planning, policy reasoning, and final reimbursement recommendation, "
        "with deterministic Python tools for receipt validation, duplicate detection, "
        "limit calculation, and approval routing."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

loader = PolicyDataLoader()
data = loader.load_all()


def create_agent(use_llm: bool = True) -> TravelReimbursementAgent:
    return TravelReimbursementAgent(
        policy_text=data["policy_text"],
        receipt_map=data["receipt_map"],
        historical_claims=data["claims"],
        approval_matrix=data["approval_matrix"],
        expense_limits=data["expense_limits"],
        use_llm=use_llm,
    )


def to_jsonable(value: Any) -> Any:
    if value is None:
        return {}

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}

    if isinstance(value, list):
        return [to_jsonable(item) for item in value]

    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]

    return value


def get_display_approval_level(result) -> str:
    if result.decision == "Reject":
        return "Not required"

    return result.approval_level


def save_evaluation_output(
    response_data: Dict[str, Any],
    claim_id: str,
    decision: str,
) -> str:
    """
    Saves one claim evaluation response as JSON inside sample_outputs/.

    Example output filenames:
    - sample_outputs/clm-001_approve.json
    - sample_outputs/clm-002_partially_approve.json
    - sample_outputs/clm-003_reject.json
    - sample_outputs/clm-004_manual_review.json
    """

    output_dir = Path("sample_outputs")
    output_dir.mkdir(exist_ok=True)

    safe_claim_id = re.sub(
        r"[^a-z0-9]+",
        "-",
        claim_id.lower(),
    ).strip("-")

    safe_decision = re.sub(
        r"[^a-z0-9]+",
        "_",
        decision.lower(),
    ).strip("_")

    output_path = output_dir / f"{safe_claim_id}_{safe_decision}.json"

    response_with_metadata = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "source": "FastAPI UI/API evaluation",
        "saved_output_path": str(output_path),
        **response_data,
    }

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(
            response_with_metadata,
            file,
            indent=2,
            ensure_ascii=False,
        )

    return str(output_path)


def build_api_response(
    result,
    agent: TravelReimbursementAgent,
    include_raw_llm: bool = False,
) -> Dict[str, Any]:
    planning_status = (
        "WORKING"
        if getattr(agent, "last_planning_llm_used", False)
        else "FAILED - deterministic fallback used"
    )

    policy_reasoning_status = (
        "WORKING"
        if getattr(agent, "last_policy_reasoning_llm_used", False)
        else "FAILED - safe policy fallback used"
    )

    final_status = (
        "WORKING"
        if getattr(agent, "last_final_llm_used", False)
        else "FAILED - deterministic fallback used"
    )

    overall_status = "WORKING" if getattr(agent, "last_llm_used", False) else "NOT USED"

    llm_tool_plan = to_jsonable(getattr(agent, "last_llm_tool_plan", {}))
    validated_tool_plan = to_jsonable(getattr(agent, "last_validated_tool_plan", {}))
    policy_reasoning = to_jsonable(getattr(agent, "last_policy_reasoning", {}))

    planning_summary = ""
    if isinstance(llm_tool_plan, dict):
        planning_summary = llm_tool_plan.get("planning_summary", "")

    policy_reasoning_summary = ""
    if isinstance(policy_reasoning, dict):
        policy_reasoning_summary = policy_reasoning.get("overall_reasoning", "")

    workflow_steps = [
        {
            "step": 1,
            "stage": "Gemini Tool Planning",
            "owner": "Gemini",
            "description": "Reads the claim and decides which reimbursement tools are required.",
        },
        {
            "step": 2,
            "stage": "Python Tool Plan Validation",
            "owner": "Python",
            "description": "Validates the selected tools and ensures mandatory checks are not skipped.",
        },
        {
            "step": 3,
            "stage": "Gemini Policy Reasoning",
            "owner": "Gemini",
            "description": "Maps each expense line to the relevant travel policy rules.",
        },
        {
            "step": 4,
            "stage": "Python Deterministic Calculation",
            "owner": "Python",
            "description": "Checks receipts, duplicates, eligibility, limits, approved amount, rejected amount, and approval routing.",
        },
        {
            "step": 5,
            "stage": "Gemini Final Recommendation",
            "owner": "Gemini",
            "description": "Uses the policy reasoning and Python-calculated evidence to generate the final reimbursement recommendation.",
        },
    ]

    response = {
        "planning_llm_used": getattr(agent, "last_planning_llm_used", False),
        "policy_reasoning_llm_used": getattr(
            agent,
            "last_policy_reasoning_llm_used",
            False,
        ),
        "final_llm_used": getattr(agent, "last_final_llm_used", False),
        "llm_used": getattr(agent, "last_llm_used", False),
        "gemini_status": {
            "planning_llm": planning_status,
            "policy_reasoning_llm": policy_reasoning_status,
            "final_llm": final_status,
            "overall": overall_status,
        },
        "ui_display": {
            "final_reimbursement_recommendation": result.explanation,
            "agent_workflow_summary": workflow_steps,
            "llm_planning_summary": planning_summary,
            "policy_reasoning_summary": policy_reasoning_summary,
            "decision": result.decision,
            "approval_level": get_display_approval_level(result),
            "submitted_amount": result.submitted_amount,
            "approved_amount": result.approved_amount,
            "rejected_amount": result.rejected_amount,
            "reason_codes": result.reason_codes,
            "manual_review_reasons": result.manual_review_reasons,
            "policy_references": result.policy_references,
        },
        "llm_policy_reasoning": policy_reasoning,
        "policy_reasoning_error": getattr(agent, "last_policy_reasoning_error", ""),
        "tools_selected_by_llm": validated_tool_plan.get(
            "llm_selected_tools",
            [],
        )
        if isinstance(validated_tool_plan, dict)
        else [],
        "tools_executed": validated_tool_plan.get(
            "final_execution_order",
            [],
        )
        if isinstance(validated_tool_plan, dict)
        else [],
        "safety_added_tools": validated_tool_plan.get(
            "skipped_by_llm_added_by_python",
            [],
        )
        if isinstance(validated_tool_plan, dict)
        else [],
        "result": result.model_dump(mode="json"),
    }

    if include_raw_llm:
        response["llm_responses"] = {
            "raw_planning_response": getattr(
                agent,
                "last_raw_planning_response",
                "",
            ),
            "raw_policy_reasoning_response": getattr(
                agent,
                "last_raw_policy_reasoning_response",
                "",
            ),
            "raw_final_response": getattr(
                agent,
                "last_raw_final_response",
                "",
            ),
        }

    return response


@app.get("/")
def health_check() -> Dict[str, Any]:
    return {
        "status": "ok",
        "message": "Travel Reimbursement Approval Agent API is running.",
        "sample_claims_loaded": len(data["claims"]),
        "receipts_loaded": len(data["receipts"]),
        "swagger_docs": "http://127.0.0.1:8000/docs",
        "simple_ui": "http://127.0.0.1:8000/ui",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/claims", response_model=List[TravelClaim])
def list_claims() -> List[TravelClaim]:
    return data["claims"]


@app.get("/claims/{claim_id}", response_model=TravelClaim)
def get_claim(claim_id: str) -> TravelClaim:
    try:
        return loader.get_claim_by_id(claim_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Claim ID not found: {claim_id}",
        )


@app.get("/evaluate/{claim_id}")
def evaluate_sample_claim(
    claim_id: str,
    use_llm: bool = Query(
        default=True,
        description="Use Gemini LLM planning, policy reasoning, and final recommendation when available.",
    ),
    include_raw_llm: bool = Query(
        default=False,
        description="Include raw Gemini planning, policy reasoning, and final response in API output.",
    ),
    save_output: bool = Query(
        default=True,
        description="Save the evaluation response as a JSON file inside sample_outputs.",
    ),
) -> Dict[str, Any]:
    try:
        claim = loader.get_claim_by_id(claim_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Claim ID not found: {claim_id}",
        )

    agent = create_agent(use_llm=use_llm)
    result = agent.evaluate_claim(claim)

    response_data = build_api_response(
        result=result,
        agent=agent,
        include_raw_llm=include_raw_llm,
    )

    if save_output:
        saved_path = save_evaluation_output(
            response_data=response_data,
            claim_id=result.claim_id,
            decision=result.decision,
        )
        response_data["saved_output_path"] = saved_path

    return response_data


@app.post("/evaluate")
def evaluate_custom_claim(
    claim: TravelClaim,
    use_llm: bool = Query(
        default=True,
        description="Use Gemini LLM planning, policy reasoning, and final recommendation when available.",
    ),
    include_raw_llm: bool = Query(
        default=False,
        description="Include raw Gemini planning, policy reasoning, and final response in API output.",
    ),
    save_output: bool = Query(
        default=True,
        description="Save the evaluation response as a JSON file inside sample_outputs.",
    ),
) -> Dict[str, Any]:
    agent = create_agent(use_llm=use_llm)
    result = agent.evaluate_claim(claim)

    response_data = build_api_response(
        result=result,
        agent=agent,
        include_raw_llm=include_raw_llm,
    )

    if save_output:
        saved_path = save_evaluation_output(
            response_data=response_data,
            claim_id=result.claim_id,
            decision=result.decision,
        )
        response_data["saved_output_path"] = saved_path

    return response_data


@app.get("/evaluate-all")
def evaluate_all_claims(
    use_llm: bool = Query(
        default=False,
        description=(
            "Evaluate all sample claims. Default is false to avoid many Gemini calls. "
            "Set true only when you want a full LLM batch run."
        ),
    ),
    include_raw_llm: bool = Query(
        default=False,
        description="Include raw Gemini responses for each claim.",
    ),
    save_output: bool = Query(
        default=True,
        description="Save each claim evaluation response as JSON inside sample_outputs.",
    ),
    delay_seconds: float = Query(
        default=5.0,
        ge=0,
        le=60,
        description=(
            "Delay between claims when evaluating all claims. "
            "Useful when use_llm=true to avoid sending Gemini requests too quickly."
        ),
    ),
) -> Dict[str, Any]:
    agent = create_agent(use_llm=use_llm)

    results = []
    total_claims = len(data["claims"])

    for index, claim in enumerate(data["claims"]):
        result = agent.evaluate_claim(claim)

        response_data = build_api_response(
            result=result,
            agent=agent,
            include_raw_llm=include_raw_llm,
        )

        if save_output:
            saved_path = save_evaluation_output(
                response_data=response_data,
                claim_id=result.claim_id,
                decision=result.decision,
            )
            response_data["saved_output_path"] = saved_path

        results.append(response_data)

        if use_llm and delay_seconds > 0 and index < total_claims - 1:
            time.sleep(delay_seconds)

    return {
        "total_claims_evaluated": len(results),
        "llm_enabled": use_llm,
        "save_output": save_output,
        "delay_seconds": delay_seconds if use_llm else 0,
        "results": results,
    }


@app.get("/ui", response_class=HTMLResponse)
def simple_ui() -> str:
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Travel Reimbursement Approval Agent</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 24px;
                background: #eef2f7;
                color: #111827;
            }

            .container {
                max-width: 1180px;
                margin: auto;
                background: white;
                padding: 24px;
                border-radius: 14px;
                box-shadow: 0 2px 14px rgba(0,0,0,0.08);
            }

            h1 {
                margin-top: 0;
                margin-bottom: 8px;
                font-size: 30px;
            }

            h2 {
                margin-top: 26px;
                margin-bottom: 10px;
                font-size: 23px;
            }

            h3 {
                margin-top: 18px;
                margin-bottom: 8px;
            }

            p {
                line-height: 1.35;
            }

            select, button {
                padding: 10px 12px;
                font-size: 15px;
                margin-right: 8px;
            }

            select {
                min-width: 300px;
            }

            button {
                cursor: pointer;
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 7px;
                font-weight: 600;
            }

            button:hover {
                background: #1d4ed8;
            }

            button:disabled {
                cursor: not-allowed;
                background: #9ca3af;
            }

            .small {
                color: #4b5563;
                font-size: 14px;
            }

            .saved-path {
                margin-top: 12px;
                color: #15803d;
                font-weight: 700;
            }

            .card {
                margin-top: 20px;
                padding: 18px;
                border-radius: 12px;
                background: #f8fafc;
                border: 1px solid #dbe3ee;
            }

            .decision-row {
                display: flex;
                flex-wrap: wrap;
                gap: 12px;
                align-items: center;
                margin-bottom: 12px;
            }

            .decision {
                display: inline-block;
                font-size: 24px;
                font-weight: 800;
                padding: 8px 14px;
                border-radius: 999px;
                background: #dbeafe;
                color: #1e40af;
            }

            .claim-meta {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 10px;
                margin-top: 12px;
            }

            .meta-box {
                background: white;
                padding: 12px;
                border-radius: 10px;
                border: 1px solid #e5e7eb;
            }

            .amounts {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 12px;
                margin-top: 14px;
            }

            .amount-box {
                background: white;
                padding: 16px;
                border-radius: 10px;
                border: 1px solid #d1d5db;
            }

            .amount-label {
                font-weight: 800;
                font-size: 17px;
            }

            .amount-value {
                font-size: 20px;
                margin-top: 4px;
            }

            .recommendation {
                background: white;
                border: 1px solid #e5e7eb;
                border-left: 5px solid #2563eb;
                padding: 16px;
                border-radius: 10px;
                font-size: 17px;
            }

            .workflow-grid {
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 10px;
                margin-top: 12px;
            }

            .workflow-step {
                background: white;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                padding: 12px;
                min-height: 126px;
            }

            .step-number {
                width: 26px;
                height: 26px;
                background: #2563eb;
                color: white;
                border-radius: 999px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-weight: 800;
                margin-bottom: 8px;
            }

            .step-title {
                font-weight: 800;
                margin-bottom: 4px;
            }

            .step-owner {
                font-size: 13px;
                color: #4b5563;
                margin-bottom: 6px;
            }

            .step-desc {
                font-size: 13px;
                color: #374151;
            }

            .progress-box {
                margin-top: 18px;
                padding: 18px;
                border-radius: 12px;
                background: #fff7ed;
                border: 1px solid #fed7aa;
            }

            .progress-title {
                font-weight: 800;
                margin-bottom: 12px;
                font-size: 18px;
            }

            .progress-step {
                display: flex;
                align-items: center;
                gap: 10px;
                padding: 8px 0;
                color: #6b7280;
            }

            .progress-step.active {
                color: #1d4ed8;
                font-weight: 800;
            }

            .progress-step.done {
                color: #15803d;
                font-weight: 700;
            }

            .progress-dot {
                width: 13px;
                height: 13px;
                border-radius: 999px;
                background: #d1d5db;
                display: inline-block;
            }

            .progress-step.active .progress-dot {
                background: #2563eb;
            }

            .progress-step.done .progress-dot {
                background: #16a34a;
            }

            details {
                background: white;
                border: 1px solid #d1d5db;
                border-radius: 10px;
                padding: 12px;
                margin-top: 12px;
            }

            summary {
                cursor: pointer;
                font-weight: 800;
                font-size: 16px;
            }

            pre {
                white-space: pre-wrap;
                background: #111827;
                color: #e5e7eb;
                padding: 14px;
                border-radius: 8px;
                overflow-x: auto;
                font-size: 13px;
            }

            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 12px;
                font-size: 14px;
            }

            th, td {
                border: 1px solid #e5e7eb;
                padding: 10px;
                text-align: left;
                vertical-align: top;
            }

            th {
                background: #f3f4f6;
            }

            .pill {
                display: inline-block;
                padding: 4px 8px;
                border-radius: 999px;
                background: #eef2ff;
                color: #3730a3;
                font-size: 12px;
                font-weight: 700;
                margin: 2px 3px 2px 0;
            }

            @media (max-width: 900px) {
                .claim-meta,
                .amounts,
                .workflow-grid {
                    grid-template-columns: 1fr;
                }

                body {
                    margin: 12px;
                }
            }
        </style>
    </head>

    <body>
        <div class="container">
            <h1>Travel Reimbursement Approval Agent</h1>

            <p class="small">
                Gemini performs tool planning, policy reasoning, and final recommendation.
                Python executes deterministic reimbursement checks and amount calculations.
            </p>

            <select id="claimId">
                <option value="CLM-001">CLM-001 - Amit Sharma</option>
                <option value="CLM-002">CLM-002 - Neha Gupta</option>
                <option value="CLM-003">CLM-003 - Ravi Kumar</option>
                <option value="CLM-004">CLM-004 - Priya Menon</option>
                <option value="CLM-005">CLM-005 - Amit Sharma</option>
                <option value="CLM-006" selected>CLM-006 - Shivaji Chatterjee</option>
            </select>

            <button id="evaluateButton" onclick="evaluateClaim()">Evaluate Claim</button>

            <p class="small">
                Swagger API docs are available at <a href="/docs" target="_blank">/docs</a>.
            </p>

            <div id="output"></div>
        </div>

        <script>
            function escapeHtml(value) {
                if (value === null || value === undefined) {
                    return "";
                }

                return String(value)
                    .replaceAll("&", "&amp;")
                    .replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;")
                    .replaceAll('"', "&quot;")
                    .replaceAll("'", "&#039;");
            }

            function formatInr(value) {
                const numberValue = Number(value || 0);

                return "INR " + numberValue.toLocaleString("en-IN", {
                    maximumFractionDigits: 2
                });
            }

            function renderProgress(currentStepIndex) {
                const steps = [
                    "Initializing claim evaluation",
                    "Gemini is planning required reimbursement tools",
                    "Gemini is mapping expenses to policy rules",
                    "Python is running deterministic checks and calculations",
                    "Gemini is generating the final reimbursement recommendation",
                    "Saving JSON output and preparing final result"
                ];

                return `
                    <div class="progress-box">
                        <div class="progress-title">Agent evaluation in progress...</div>
                        ${steps.map((step, index) => {
                            let className = "progress-step";

                            if (index < currentStepIndex) {
                                className += " done";
                            } else if (index === currentStepIndex) {
                                className += " active";
                            }

                            return `
                                <div class="${className}">
                                    <span class="progress-dot"></span>
                                    <span>${escapeHtml(step)}</span>
                                </div>
                            `;
                        }).join("")}
                    </div>
                `;
            }

            function startProgress(outputElement) {
                let currentStep = 0;

                outputElement.innerHTML = renderProgress(currentStep);

                const timer = setInterval(() => {
                    if (currentStep < 5) {
                        currentStep += 1;
                        outputElement.innerHTML = renderProgress(currentStep);
                    }
                }, 1100);

                return timer;
            }

            function renderWorkflowSteps(steps) {
                if (!Array.isArray(steps) || !steps.length) {
                    return "<p>No workflow summary available.</p>";
                }

                return `
                    <div class="workflow-grid">
                        ${steps.map(step => `
                            <div class="workflow-step">
                                <div class="step-number">${escapeHtml(step.step)}</div>
                                <div class="step-title">${escapeHtml(step.stage)}</div>
                                <div class="step-owner">${escapeHtml(step.owner)}</div>
                                <div class="step-desc">${escapeHtml(step.description)}</div>
                            </div>
                        `).join("")}
                    </div>
                `;
            }

            function renderPills(items) {
                if (!Array.isArray(items) || !items.length) {
                    return "<span class='small'>None</span>";
                }

                return items.map(item => `<span class="pill">${escapeHtml(item)}</span>`).join("");
            }

            function renderExpenseDecisions(expenses) {
                if (!Array.isArray(expenses) || !expenses.length) {
                    return "<p>No expense decisions available.</p>";
                }

                return `
                    <table>
                        <thead>
                            <tr>
                                <th>Expense</th>
                                <th>Category</th>
                                <th>Submitted</th>
                                <th>Approved</th>
                                <th>Rejected</th>
                                <th>Status</th>
                                <th>Reason</th>
                                <th>Policies</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${expenses.map(item => `
                                <tr>
                                    <td>${escapeHtml(item.expense_id)}</td>
                                    <td>${escapeHtml(item.category)}</td>
                                    <td>${formatInr(item.submitted_amount)}</td>
                                    <td>${formatInr(item.approved_amount)}</td>
                                    <td>${formatInr(item.rejected_amount)}</td>
                                    <td>${escapeHtml(String(item.status || "").replaceAll("_", " "))}</td>
                                    <td>${escapeHtml(item.reason)}</td>
                                    <td>${renderPills(item.policy_references || [])}</td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table>
                `;
            }

            async function evaluateClaim() {
                const claimId = document.getElementById("claimId").value;
                const output = document.getElementById("output");
                const evaluateButton = document.getElementById("evaluateButton");

                const progressTimer = startProgress(output);

                if (evaluateButton) {
                    evaluateButton.disabled = true;
                }

                try {
                    const response = await fetch(
                        `/evaluate/${claimId}?use_llm=true&include_raw_llm=false&save_output=true`
                    );

                    const data = await response.json();

                    if (!response.ok) {
                        output.innerHTML = `<p style="color:red;">Error: ${escapeHtml(JSON.stringify(data))}</p>`;
                        return;
                    }

                    const ui = data.ui_display || {};
                    const result = data.result || {};

                    output.innerHTML = `
                        <div class="card">
                            <div class="decision-row">
                                <div class="decision">${escapeHtml(ui.decision)}</div>
                            </div>

                            <div class="claim-meta">
                                <div class="meta-box">
                                    <strong>Claim ID</strong><br>
                                    ${escapeHtml(result.claim_id)}
                                </div>
                                <div class="meta-box">
                                    <strong>Employee</strong><br>
                                    ${escapeHtml(result.employee_name)}
                                </div>
                                <div class="meta-box">
                                    <strong>Approval Level</strong><br>
                                    ${escapeHtml(ui.approval_level)}
                                </div>
                            </div>

                            <div class="amounts">
                                <div class="amount-box">
                                    <div class="amount-label">Submitted</div>
                                    <div class="amount-value">${formatInr(ui.submitted_amount)}</div>
                                </div>
                                <div class="amount-box">
                                    <div class="amount-label">Approved</div>
                                    <div class="amount-value">${formatInr(ui.approved_amount)}</div>
                                </div>
                                <div class="amount-box">
                                    <div class="amount-label">Rejected</div>
                                    <div class="amount-value">${formatInr(ui.rejected_amount)}</div>
                                </div>
                            </div>

                            <h2>Final Reimbursement Recommendation</h2>
                            <div class="recommendation">
                                ${escapeHtml(ui.final_reimbursement_recommendation)}
                            </div>

                            ${
                                data.saved_output_path
                                ? `<p class="saved-path">JSON saved to: ${escapeHtml(data.saved_output_path)}</p>`
                                : ""
                            }

                            <details>
                                <summary>View Gemini Planning Details</summary>
                                <h3>Planning Summary</h3>
                                <p>${escapeHtml(ui.llm_planning_summary || "No planning summary available.")}</p>

                                <h3>Tools Selected by Gemini</h3>
                                ${renderPills(data.tools_selected_by_llm || [])}

                                <h3>Safety Tools Added by Python</h3>
                                ${renderPills(data.safety_added_tools || [])}
                            </details>

                            <details>
                                <summary>View Gemini Policy Reasoning Details</summary>
                                <h3>Policy Reasoning Summary</h3>
                                <p>${escapeHtml(ui.policy_reasoning_summary || "No policy reasoning summary available.")}</p>

                                <h3>Policy References</h3>
                                ${renderPills(ui.policy_references || [])}

                                <h3>Policy Reasoning JSON</h3>
                                <pre>${escapeHtml(JSON.stringify(data.llm_policy_reasoning || {}, null, 2))}</pre>
                            </details>

                            <details>
                                <summary>View Python Tool Execution Details</summary>
                                <h3>Tools Executed</h3>
                                ${renderPills(data.tools_executed || [])}

                                <h3>Reason Codes</h3>
                                ${renderPills(ui.reason_codes || [])}

                                <h3>Manual Review Reasons</h3>
                                ${
                                    ui.manual_review_reasons && ui.manual_review_reasons.length
                                    ? `<p>${escapeHtml(ui.manual_review_reasons.join("; "))}</p>`
                                    : "<p>None</p>"
                                }
                            </details>

                            <details>
                                <summary>View Expense Decision Details</summary>
                                ${renderExpenseDecisions(result.expense_decisions || [])}
                            </details>

                            <details>
                                <summary>View Agent Workflow Summary</summary>
                                ${renderWorkflowSteps(ui.agent_workflow_summary)}
                            </details>

                            <details>
                                <summary>View Full Audit JSON</summary>
                                <pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>
                            </details>
                        </div>
                    `;
                } catch (error) {
                    output.innerHTML = `<p style="color:red;">Error: ${escapeHtml(error)}</p>`;
                } finally {
                    clearInterval(progressTimer);

                    if (evaluateButton) {
                        evaluateButton.disabled = false;
                    }
                }
            }
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )