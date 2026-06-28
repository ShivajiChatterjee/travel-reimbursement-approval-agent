from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


DecisionType = Literal["Approve", "Partially Approve", "Reject", "Manual Review"]


class ExpenseItem(BaseModel):
    expense_id: str
    date: date
    category: str
    vendor: str
    amount: float
    receipt_id: Optional[str] = None
    travel_class: Optional[str] = None
    number_of_nights: Optional[int] = None
    prior_approval_available: Optional[bool] = None


class TravelClaim(BaseModel):
    claim_id: str
    employee_id: str
    employee_name: str
    travel_type: Optional[str] = None
    origin_country: Optional[str] = None
    destination_country: Optional[str] = None
    business_purpose: str
    start_date: date
    end_date: date
    expenses: List[ExpenseItem]


class Receipt(BaseModel):
    receipt_id: str
    vendor: str
    date: date
    amount: float
    category: str
    attachment_available: bool = True


class ApprovalThreshold(BaseModel):
    min_amount: float
    max_amount: Optional[float] = None
    approval_level: str
    manual_review_required: bool
    policy_reference: str
    description: str


class ApprovalMatrix(BaseModel):
    approval_thresholds: List[ApprovalThreshold]


class ExpenseLimit(BaseModel):
    limit_type: str
    max_amount: Optional[float] = None
    requires_number_of_nights: Optional[bool] = False
    requires_number_of_days: Optional[bool] = False
    assumption: Optional[str] = None
    policy_reference: str


class ExpenseLimitsConfig(BaseModel):
    currency: str = "INR"
    domestic: Dict[str, ExpenseLimit]
    international: Dict[str, ExpenseLimit]


class ToolResult(BaseModel):
    tool_name: str
    status: str
    passed: bool
    details: Dict[str, Any] = Field(default_factory=dict)
    policy_references: List[str] = Field(default_factory=list)


class ExpenseDecision(BaseModel):
    expense_id: str
    category: str
    submitted_amount: float
    approved_amount: float
    rejected_amount: float
    status: str
    reason: str
    policy_references: List[str] = Field(default_factory=list)


class EvaluationResponse(BaseModel):
    claim_id: str
    employee_id: str
    employee_name: str
    decision: DecisionType
    submitted_amount: float
    approved_amount: float
    rejected_amount: float
    approval_level: str
    missing_documents: List[str] = Field(default_factory=list)
    manual_review_reasons: List[str] = Field(default_factory=list)
    policy_references: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    explanation: str
    reason_codes: List[str] = Field(default_factory=list)
    expense_decisions: List[ExpenseDecision] = Field(default_factory=list)
    tool_plan: Dict[str, Any] = Field(default_factory=dict)
    tools_called: List[ToolResult] = Field(default_factory=list)


class PlannedToolCall(BaseModel):
    tool_name: str
    reason: str
    required: bool = True


class LLMToolPlan(BaseModel):
    selected_tools: List[PlannedToolCall] = Field(default_factory=list)
    missing_or_conflicting_info: List[str] = Field(default_factory=list)
    planning_summary: str = ""


class ExpensePolicyMatch(BaseModel):
    """
    Gemini's policy reasoning result for one expense line.

    This model does not calculate money.
    It only captures which policies Gemini thinks apply to an expense.
    """

    expense_id: str
    category: str
    applicable_policy_ids: List[str] = Field(default_factory=list)
    policy_reasoning: str = ""

    decision_signal: str = Field(
        default="unclear",
        description=(
            "Gemini's reasoning signal for this expense. "
            "Examples: eligible, non_reimbursable, limit_check_required, "
            "receipt_check_required, duplicate_check_required, manual_review, unclear."
        ),
    )

    requires_calculation: bool = True
    manual_review_required: bool = False
    risk_flags: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.70, ge=0, le=1)


class PolicyReasoningResponse(BaseModel):
    """
    Gemini's claim-to-policy mapping.

    Gemini reads the claim and travel policy document, then returns:
    - which policies apply to each expense
    - why those policies apply
    - whether any expense needs manual review
    - claim-level policy IDs
    """

    claim_id: str
    expense_policy_matches: List[ExpensePolicyMatch] = Field(default_factory=list)
    claim_level_policy_ids: List[str] = Field(default_factory=list)
    missing_or_conflicting_info: List[str] = Field(default_factory=list)
    overall_reasoning: str = ""
    confidence: float = Field(default=0.70, ge=0, le=1)