# Company Travel Reimbursement Policy

## POL-001: General Reimbursement Eligibility

Employees may claim reimbursement only for business-related travel expenses.

Each claim must include:
- Employee ID
- Employee name
- Business purpose
- Travel type
- Travel dates
- Expense category
- Expense amount
- Vendor details
- Receipt details where applicable

Travel is classified as domestic when both origin_country and destination_country are India.

Travel is classified as international when either origin_country or destination_country is outside India.

If the travel_type field is missing, unclear, or inconsistent with the origin_country and destination_country values, the claim must be routed to Manual Review.

Claims with missing, unclear, or conflicting information must be routed to Manual Review.

---

## POL-002: Receipt Requirement

Receipts are mandatory for any individual expense greater than INR 500.

A valid receipt must include:
- Receipt ID
- Vendor name
- Transaction date
- Amount
- Expense category
- Attachment availability

If a required receipt is missing, unclear, mismatched, or the receipt attachment is unavailable, the claim should be routed to Manual Review.

Expenses of INR 500 or below may be reimbursed without a receipt if the expense category is eligible and the business purpose is valid.

---

## POL-003: Eligible Expense Categories

The following categories are eligible for reimbursement:
- flight
- hotel
- meals
- taxi
- train
- conference

Expenses outside these categories are not reimbursable unless explicitly approved.

---

## POL-004: Non-Reimbursable Expenses

The following expense categories are not reimbursable:
- alcohol
- personal shopping
- entertainment
- fines or penalties
- family travel
- luxury upgrades without approval

Claims containing non-reimbursable categories should be rejected for those specific expense lines.

If all expense lines in a claim are non-reimbursable, the entire claim should be rejected.

---

## POL-005: Domestic Travel Limits

For domestic travel, the following limits apply:

- hotel: INR 8,000 per night
- meals: INR 1,500 per day
- taxi: INR 3,000 per day
- train: Actual amount with valid receipt
- flight: Economy class only
- conference: Actual registration cost with valid receipt and business justification

Hotel limits apply per night.

Multi-night hotel claims should either be itemized per night or include the number of nights.

If the number of nights is missing for a hotel claim, the claim should be routed to Manual Review.

For this prototype, each meals and taxi expense line is treated as a single day's expense for limit calculation.

Expenses above the allowed category limit may be partially approved up to the policy limit.

---

## POL-006: International Travel Limits

For international travel, the following limits apply:

- hotel: INR 18,000 per night
- meals: INR 3,500 per day
- taxi: INR 6,000 per day
- train: Actual amount with valid receipt
- flight: Economy class only
- conference: Actual registration cost with valid receipt and business justification

Hotel limits apply per night.

Multi-night hotel claims should either be itemized per night or include the number of nights.

If the number of nights is missing for a hotel claim, the claim should be routed to Manual Review.

For this prototype, each meals and taxi expense line is treated as a single day's expense for limit calculation.

Expenses above the allowed category limit may be partially approved up to the policy limit.

---

## POL-007: Flight Class Rule

Only economy-class flight tickets are reimbursable by default.

Business-class, premium-economy, or first-class tickets require prior approval.

If the class of travel is missing for a flight claim, the claim should be routed to Manual Review.

If the flight class is business, premium-economy, or first-class and no prior approval is available, the claim should be routed to Manual Review.

---

## POL-008: Duplicate Claim Rule

If a claim has the same employee ID, expense date, vendor, category, and amount as an earlier submitted claim, it should be treated as a duplicate.

Duplicate claims must be rejected.

If only some expense lines are duplicate, those duplicate expense lines should be rejected and the remaining valid expense lines may still be evaluated.

---

## POL-009: Approval Thresholds

Claims up to INR 25,000 may be approved by the reporting manager.

Claims above INR 25,000 and up to INR 100,000 require finance approval.

Claims above INR 100,000 require director approval.

Finance approval and manager approval are internal routing steps. The agent should still return Approve or Partially Approve for eligible claims within these thresholds, while noting the required approval level in the explanation.

In this prototype, claims above INR 100,000 should be routed to Manual Review.

---

## POL-010: Decision Rules

A claim should be marked as Approve when:
- All expenses are eligible
- Required receipts are complete
- Amounts are within policy limits
- No duplicate claim is detected
- No manual approval exception is needed

A claim should be marked as Partially Approve when:
- Some expenses exceed category limits
- Some expense lines are non-reimbursable
- Some expense lines are duplicates
- The remaining valid expenses can still be reimbursed

A claim should be marked as Reject when:
- The claim is duplicate
- All expense lines are non-reimbursable
- The claim violates core reimbursement rules
- No amount is eligible for reimbursement

A claim should be marked as Manual Review when:
- Required information is missing
- Receipt evidence is incomplete
- Receipt details are mismatched
- Travel type is missing or unclear
- Hotel claim does not specify number of nights
- Travel class is missing for flight claims
- Prior approval exception is required
- Claim amount is above INR 100,000
- Policy conflict or uncertainty exists

---

## POL-011: Approved and Rejected Amount Calculation

The approved amount should include only the reimbursable amount allowed under policy.

The rejected amount should include:
- Non-reimbursable expense amounts
- Amounts above category limits
- Duplicate expense amounts
- Expense amounts rejected due to policy violation

For partially approved claims, the approved amount and rejected amount must be clearly separated.

---

## POL-012: Confidence and Explanation

The agent should provide a confidence score between 0 and 1.

High confidence should be used when:
- Policy rules clearly apply
- Required receipts are available
- No conflicting information exists

Lower confidence should be used when:
- Information is incomplete
- Policy interpretation is uncertain
- Manual review is required

Each decision should include a short explanation and reference the relevant policy rule IDs.