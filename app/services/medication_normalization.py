import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.drug_class_registry import (
    DEFAULT_DRUG_CLASS_REGISTRY,
    DrugClassRegistry,
)

_NAME_SYNONYMS: dict[str, str] = {
    "tylenol": "acetaminophen",
    "paracetamol": "acetaminophen",
    "advil": "ibuprofen",
    "glucophage": "metformin",
}

_DRUG_FORM_TOKENS = {
    "tab",
    "tabs",
    "tablet",
    "tablets",
    "cap",
    "caps",
    "capsule",
    "capsules",
    "oral",
    "solution",
    "suspension",
    "injection",
    "injectable",
}

_UNIT_ALIASES: dict[str, tuple[str, Decimal]] = {
    "mcg": ("mg", Decimal("0.001")),
    "ug": ("mg", Decimal("0.001")),
    "microgram": ("mg", Decimal("0.001")),
    "micrograms": ("mg", Decimal("0.001")),
    "mg": ("mg", Decimal("1")),
    "milligram": ("mg", Decimal("1")),
    "milligrams": ("mg", Decimal("1")),
    "g": ("mg", Decimal("1000")),
    "gram": ("mg", Decimal("1000")),
    "grams": ("mg", Decimal("1000")),
    "ml": ("ml", Decimal("1")),
    "milliliter": ("ml", Decimal("1")),
    "milliliters": ("ml", Decimal("1")),
    "l": ("ml", Decimal("1000")),
    "liter": ("ml", Decimal("1000")),
    "liters": ("ml", Decimal("1000")),
    "unit": ("unit", Decimal("1")),
    "units": ("unit", Decimal("1")),
    "iu": ("unit", Decimal("1")),
}

_DOSE_PATTERN = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]+)?\s*$")


class NormalizedMedication(BaseModel):
    original_name: str = Field(min_length=1, max_length=255)
    canonical_name: str = Field(min_length=1, max_length=255)
    original_dose: str | None = Field(default=None, max_length=120)
    canonical_dose_value: Decimal | None = None
    canonical_unit: str | None = Field(default=None, max_length=20)
    canonical_dose: str | None = Field(default=None, max_length=80)
    drug_classes: tuple[str, ...] = Field(default_factory=tuple)

    model_config = ConfigDict(str_strip_whitespace=True)


class MedicationNormalizationService:
    def __init__(
        self,
        registry: DrugClassRegistry = DEFAULT_DRUG_CLASS_REGISTRY,
    ) -> None:
        self.registry = registry

    def normalize(
        self,
        drug_name: str,
        dose: str | int | float | Decimal | None = None,
        unit: str | None = None,
    ) -> NormalizedMedication:
        canonical_name = normalize_drug_name(drug_name)
        dose_value, canonical_unit = normalize_dose_and_unit(dose, unit)
        canonical_dose = format_canonical_dose(dose_value, canonical_unit)
        classes = self.registry.get_classes_for_drug(canonical_name)

        original_dose = str(dose).strip() if dose is not None else None

        return NormalizedMedication(
            original_name=drug_name,
            canonical_name=canonical_name,
            original_dose=original_dose,
            canonical_dose_value=dose_value,
            canonical_unit=canonical_unit,
            canonical_dose=canonical_dose,
            drug_classes=classes,
        )

    def normalize_batch(
        self,
        medications: list[dict[str, Any]],
    ) -> list[NormalizedMedication]:
        normalized: list[NormalizedMedication] = []
        for entry in medications:
            normalized.append(
                self.normalize(
                    drug_name=str(entry.get("name", "")),
                    dose=entry.get("dose"),
                    unit=entry.get("unit"),
                )
            )

        return normalized


def normalize_drug_name(drug_name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", drug_name.strip().lower())
    tokens = [token for token in cleaned.split() if token]
    normalized_tokens: list[str] = []

    for token in tokens:
        if token in _DRUG_FORM_TOKENS:
            continue
        if token in _UNIT_ALIASES:
            continue
        if token.replace(".", "", 1).isdigit():
            continue
        normalized_tokens.append(token)

    collapsed = " ".join(normalized_tokens)
    return _NAME_SYNONYMS.get(collapsed, collapsed)


def normalize_dose_and_unit(
    dose: str | int | float | Decimal | None,
    unit: str | None = None,
) -> tuple[Decimal | None, str | None]:
    if dose is None and unit is None:
        return None, None

    if dose is None:
        raise ValueError("Dose value is required when unit is provided.")

    parsed_value, parsed_raw_unit = _parse_dose_input(dose)

    if parsed_raw_unit and unit:
        if _unit_signature(parsed_raw_unit) != _unit_signature(unit):
            raise ValueError("Dose unit conflicts with explicit unit argument.")

    source_unit = parsed_raw_unit or unit
    if source_unit is None:
        return parsed_value, None

    converted_value, converted_unit = _convert_to_canonical(parsed_value, source_unit)
    return converted_value, converted_unit


def format_canonical_dose(value: Decimal | None, unit: str | None) -> str | None:
    if value is None:
        return None

    if unit is None:
        return _decimal_to_str(value)

    return f"{_decimal_to_str(value)} {unit}"


def _parse_dose_input(dose: str | int | float | Decimal) -> tuple[Decimal, str | None]:
    if isinstance(dose, Decimal):
        return dose, None

    if isinstance(dose, (int, float)):
        return Decimal(str(dose)), None

    match = _DOSE_PATTERN.match(dose)
    if match is None:
        raise ValueError("Dose must be numeric or include a numeric value plus unit.")

    raw_value = match.group("value")
    raw_unit = match.group("unit")

    try:
        value = Decimal(raw_value)
    except InvalidOperation as error:
        raise ValueError("Dose value is invalid.") from error

    raw_unit_normalized = raw_unit.lower() if raw_unit else None
    if raw_unit_normalized:
        _unit_signature(raw_unit_normalized)

    return value, raw_unit_normalized


def _unit_signature(raw_unit: str) -> tuple[str, Decimal]:
    normalized = raw_unit.strip().lower()
    if normalized not in _UNIT_ALIASES:
        raise ValueError(f"Unsupported medication unit: {raw_unit}")

    return _UNIT_ALIASES[normalized]


def _convert_to_canonical(value: Decimal, raw_unit: str) -> tuple[Decimal, str]:
    canonical_unit, multiplier = _unit_signature(raw_unit)
    return value * multiplier, canonical_unit


def _decimal_to_str(value: Decimal) -> str:
    normalized = value.normalize()
    rendered = format(normalized, "f")
    if "." in rendered:
        return rendered.rstrip("0").rstrip(".") or "0"

    return rendered
