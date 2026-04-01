"""
FOCUS 1.0 passthrough normalizer.

Validates and parses files already in FOCUS 1.0 format (CSV or Parquet).
Returns both valid records and per-row validation errors.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from focus_schema.models import FocusRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation error model
# ---------------------------------------------------------------------------

@dataclass
class RowValidationError:
    """Represents a validation failure for a single row."""
    row_number: int
    field: str | None
    message: str
    raw_value: Any = None


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _parse_csv_row(row: dict[str, str]) -> dict:
    """
    Convert a CSV row dict (all strings) to a dict suitable for FocusRecord.
    Handles type coercion for numeric and datetime fields.
    """
    result: dict[str, Any] = {}
    numeric_fields = {
        "EffectiveCost", "ListCost", "ContractedCost", "ContractedUnitPrice",
        "ListUnitPrice", "ConsumedQuantity", "PricingQuantity",
    }
    for key, value in row.items():
        if not value or value.strip() == "":
            result[key] = None
        elif key in numeric_fields:
            try:
                result[key] = float(value.strip())
            except ValueError:
                result[key] = value
        elif key == "Tags":
            # Tags may be JSON or key=value pairs
            import json
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                # Try key=value;key=value format
                tags: dict[str, str] = {}
                for pair in value.split(";"):
                    if "=" in pair:
                        k, _, v = pair.partition("=")
                        tags[k.strip()] = v.strip()
                result[key] = tags if tags else None
        else:
            result[key] = value.strip()
    return result


def parse_focus_file(
    file_path: str,
) -> tuple[list[FocusRecord], list[RowValidationError]]:
    """
    Parse a file in FOCUS 1.0 format (CSV or Parquet).

    Validates each row against the FocusRecord Pydantic schema.
    Partial records are accepted if all required FOCUS fields are present.

    Args:
        file_path: Path to a FOCUS 1.0 CSV or Parquet file.

    Returns:
        Tuple of (valid_records, validation_errors).
        validation_errors contains one entry per failed row with field-level detail.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in (".parquet", ".pq"):
        return _parse_parquet(path)
    elif suffix in (".csv", ".tsv", ".txt"):
        return _parse_csv(path)
    else:
        raise ValueError(
            f"Unsupported file format: {suffix}. "
            "Supported formats: .csv, .tsv, .parquet, .pq"
        )


def _parse_csv(path: Path) -> tuple[list[FocusRecord], list[RowValidationError]]:
    records: list[FocusRecord] = []
    errors: list[RowValidationError] = []

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row_num, raw_row in enumerate(reader, start=2):  # 1-indexed, row 1 is header
            row_data = _parse_csv_row(raw_row)
            _validate_row(row_num, row_data, records, errors)

    logger.info(
        "FOCUS CSV parsed: %d valid, %d errors from %s",
        len(records), len(errors), path,
    )
    return records, errors


def _parse_parquet(path: Path) -> tuple[list[FocusRecord], list[RowValidationError]]:
    import pyarrow.parquet as pq

    records: list[FocusRecord] = []
    errors: list[RowValidationError] = []

    table = pq.read_table(str(path))
    df = table.to_pydict()

    num_rows = len(next(iter(df.values()), []))
    for row_num in range(num_rows):
        row_data = {col: df[col][row_num] for col in df}
        _validate_row(row_num + 1, row_data, records, errors)

    logger.info(
        "FOCUS Parquet parsed: %d valid, %d errors from %s",
        len(records), len(errors), path,
    )
    return records, errors


def _validate_row(
    row_num: int,
    row_data: dict,
    records: list[FocusRecord],
    errors: list[RowValidationError],
) -> None:
    """Validate a single row and append to records or errors."""
    try:
        record = FocusRecord.model_validate(row_data)
        records.append(record)
    except PydanticValidationError as exc:
        for error in exc.errors():
            field_path = ".".join(str(loc) for loc in error["loc"]) if error["loc"] else None
            errors.append(RowValidationError(
                row_number=row_num,
                field=field_path,
                message=error["msg"],
                raw_value=error.get("input"),
            ))
    except Exception as exc:
        errors.append(RowValidationError(
            row_number=row_num,
            field=None,
            message=str(exc),
        ))
