import json
from pathlib import Path
from typing import Dict, List, Optional

from app.models import (
    ApprovalMatrix,
    ExpenseLimitsConfig,
    Receipt,
    TravelClaim,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class PolicyDataLoader:
    """
    Loads and validates all local mock data required by the
    Travel Reimbursement Approval Agent.
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DATA_DIR

    def _load_json(self, file_name: str):
        file_path = self.data_dir / file_name

        if not file_path.exists():
            raise FileNotFoundError(f"Required data file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def load_policy_text(self) -> str:
        file_path = self.data_dir / "travel_policy.md"

        if not file_path.exists():
            raise FileNotFoundError(f"Policy file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()

    def load_claims(self) -> List[TravelClaim]:
        raw_claims = self._load_json("claims.json")
        return [TravelClaim.model_validate(claim) for claim in raw_claims]

    def load_receipts(self) -> List[Receipt]:
        raw_receipts = self._load_json("receipts.json")
        return [Receipt.model_validate(receipt) for receipt in raw_receipts]

    def load_approval_matrix(self) -> ApprovalMatrix:
        raw_matrix = self._load_json("approval_matrix.json")
        return ApprovalMatrix.model_validate(raw_matrix)

    def load_expense_limits(self) -> ExpenseLimitsConfig:
        raw_limits = self._load_json("expense_limits.json")
        return ExpenseLimitsConfig.model_validate(raw_limits)

    def get_claim_by_id(self, claim_id: str) -> TravelClaim:
        claims = self.load_claims()

        for claim in claims:
            if claim.claim_id == claim_id:
                return claim

        raise ValueError(f"Claim ID not found: {claim_id}")

    def get_receipt_map(self) -> Dict[str, Receipt]:
        receipts = self.load_receipts()
        return {receipt.receipt_id: receipt for receipt in receipts}

    def load_all(self) -> Dict:
        """
        Loads all data needed by the agent in one call.
        This keeps agent.py clean and avoids repeated file loading logic.
        """
        claims = self.load_claims()
        receipts = self.load_receipts()

        return {
            "policy_text": self.load_policy_text(),
            "claims": claims,
            "receipts": receipts,
            "receipt_map": {receipt.receipt_id: receipt for receipt in receipts},
            "approval_matrix": self.load_approval_matrix(),
            "expense_limits": self.load_expense_limits(),
        }


if __name__ == "__main__":
    loader = PolicyDataLoader()
    data = loader.load_all()

    print("Policy loader test successful")
    print(f"Claims loaded:               {len(data['claims'])}")
    print(f"Receipts loaded:             {len(data['receipts'])}")
    print(f"Approval thresholds loaded:  {len(data['approval_matrix'].approval_thresholds)}")
    print(f"Expense limit currency:      {data['expense_limits'].currency}")
    print(f"Policy text length:          {len(data['policy_text'])} characters")
    print(f"Receipt map keys:            {list(data['receipt_map'].keys())[:3]} ...")