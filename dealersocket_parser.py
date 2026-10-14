"""
DealerSocket Lead Data Normalizer
---------------------------------
Parses downloaded DealerSocket Excel/CSV reports into structured lead records
matching the Matador Replacement Scope of Work (Section 6.2 Payload):
- Opportunity ID / Customer ID
- Rooftop entity (Cranbourne MG, Cranbourne Kia, Geely Cranbourne)
- Customer Name, Normalised Australian Mobile, Email
- Vehicle of Interest
- Lead Source
- Assigned Salesperson
- Lead Received Timestamp
"""

import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd

logger = logging.getLogger("DealerSocketParser")

# Rooftop mapping keywords
ROOFTOP_MAP = {
    "kia": "Cranbourne Kia",
    "cranbourne kia": "Cranbourne Kia",
    "mg": "Cranbourne MG",
    "cranbourne mg": "Cranbourne MG",
    "geely": "Geely Cranbourne",
    "geely cranbourne": "Geely Cranbourne",
}


def normalize_australian_mobile(phone: Any) -> Optional[str]:
    """
    Normalizes Australian mobile numbers into standard format: +614XXXXXXXX or 04XXXXXXXX.
    Returns None if not a valid mobile.
    """
    if pd.isna(phone):
        return None
    raw = str(phone).strip()
    digits = re.sub(r"[^\d+]", "", raw)

    if digits.startswith("+614") and len(digits) == 12:
        return digits
    elif digits.startswith("614") and len(digits) == 11:
        return f"+{digits}"
    elif digits.startswith("04") and len(digits) == 10:
        return f"+61{digits[1:]}"
    elif digits.startswith("4") and len(digits) == 9:
        return f"+61{digits}"

    return digits  # fallback to raw digits if non-standard


def detect_rooftop(value: Any) -> str:
    """
    Identifies if a record belongs to Cranbourne MG, Cranbourne Kia, or Geely Cranbourne.
    """
    if pd.isna(value):
        return "Unknown"
    text = str(value).lower()
    for key, rooftop_name in ROOFTOP_MAP.items():
        if key in text:
            return rooftop_name
    return str(value)


def parse_dealersocket_excel(file_path: Path) -> List[Dict[str, Any]]:
    """
    Reads an exported DealerSocket Excel/CSV file and maps its columns
    to the standardized lead schema.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if file_path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(file_path)
    elif file_path.suffix.lower() == ".csv":
        df = pd.read_csv(file_path)
    else:
        raise ValueError(f"Unsupported file format: {file_path.suffix}")

    # Standardize column headers (lowercase, trimmed)
    df.columns = [str(c).strip().lower() for c in df.columns]

    leads = []
    for _, row in df.iterrows():
        # Heuristic column match
        opp_id = (
            row.get("opportunity id")
            or row.get("opportunity_id")
            or row.get("id")
            or row.get("opp id")
        )
        customer_name = (
            row.get("customer")
            or row.get("customer name")
            or row.get("name")
            or f"{row.get('first name', '')} {row.get('last name', '')}".strip()
        )
        phone = (
            row.get("mobile")
            or row.get("cell phone")
            or row.get("phone")
            or row.get("customer phone")
        )
        email = row.get("email") or row.get("customer email")
        rooftop_raw = (
            row.get("store")
            or row.get("dealership")
            or row.get("rooftop")
            or row.get("entity")
            or row.get("location")
        )
        vehicle = (
            row.get("vehicle")
            or row.get("vehicle of interest")
            or row.get("car")
            or row.get("model")
        )
        source = row.get("source") or row.get("lead source")
        salesperson = (
            row.get("salesperson")
            or row.get("sales rep")
            or row.get("owner")
            or row.get("assigned to")
        )
        received_at = (
            row.get("created")
            or row.get("created date")
            or row.get("lead date")
            or row.get("received date")
        )

        record = {
            "opportunity_id": str(opp_id) if pd.notna(opp_id) else None,
            "customer_name": str(customer_name) if pd.notna(customer_name) else None,
            "mobile": normalize_australian_mobile(phone),
            "email": str(email) if pd.notna(email) else None,
            "rooftop": detect_rooftop(rooftop_raw),
            "vehicle_of_interest": str(vehicle) if pd.notna(vehicle) else "Vehicle of interest",
            "lead_source": str(source) if pd.notna(source) else "Website",
            "salesperson": str(salesperson) if pd.notna(salesperson) else "Unassigned",
            "received_at": str(received_at) if pd.notna(received_at) else None,
            "raw": row.to_dict(),
        }
        leads.append(record)

    logger.info(f"Successfully parsed {len(leads)} leads from {file_path.name}")
    return leads


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target_file = Path(sys.argv[1])
        parsed = parse_dealersocket_excel(target_file)
        print(f"Parsed {len(parsed)} leads:")
        for lead in parsed[:5]:
            print(lead)
    else:
        print("Usage: python dealersocket_parser.py <path_to_excel_file>")
