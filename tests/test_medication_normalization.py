from decimal import Decimal

import pytest

from app.services import MedicationNormalizationService
from app.services.drug_class_registry import DEFAULT_DRUG_CLASS_REGISTRY


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("Tylenol 500 mg tablet", "acetaminophen"),
        ("  LISINOPRIL   10 MG TAB ", "lisinopril"),
        ("Potassium-Chloride oral solution", "potassium chloride"),
    ],
)
def test_normalize_drug_name_to_canonical_form(raw_name: str, expected: str) -> None:
    service = MedicationNormalizationService()

    result = service.normalize(raw_name)

    assert result.canonical_name == expected


@pytest.mark.parametrize(
    ("dose", "unit", "expected_value", "expected_unit", "expected_rendered"),
    [
        ("500mcg", None, Decimal("0.5"), "mg", "0.5 mg"),
        ("0.5", "g", Decimal("500"), "mg", "500 mg"),
        (5, "mg", Decimal("5"), "mg", "5 mg"),
        ("2", "liters", Decimal("2000"), "ml", "2000 ml"),
    ],
)
def test_normalize_dose_and_unit_to_canonical_form(
    dose: str | int,
    unit: str | None,
    expected_value: Decimal,
    expected_unit: str,
    expected_rendered: str,
) -> None:
    service = MedicationNormalizationService()

    result = service.normalize("metformin", dose=dose, unit=unit)

    assert result.canonical_dose_value == expected_value
    assert result.canonical_unit == expected_unit
    assert result.canonical_dose == expected_rendered


def test_drug_classes_are_attached_from_static_registry() -> None:
    service = MedicationNormalizationService()

    result = service.normalize("aspirin")

    assert set(result.drug_classes) == {"antiplatelet", "nsaid"}


def test_registry_returns_known_conflicting_class_pair() -> None:
    classes = DEFAULT_DRUG_CLASS_REGISTRY.get_classes_for_drugs(
        ["lisinopril", "losartan", "atorvastatin"]
    )

    conflicts = DEFAULT_DRUG_CLASS_REGISTRY.find_conflicting_pairs(classes)

    assert ("ace_inhibitor", "arb") in conflicts


@pytest.mark.parametrize(
    ("dose", "unit", "error_fragment"),
    [
        ("10", "drops", "Unsupported medication unit"),
        ("5 mg", "ml", "Dose unit conflicts"),
        (None, "mg", "Dose value is required"),
    ],
)
def test_normalize_rejects_invalid_or_conflicting_units(
    dose: str | None,
    unit: str | None,
    error_fragment: str,
) -> None:
    service = MedicationNormalizationService()

    with pytest.raises(ValueError, match=error_fragment):
        service.normalize("warfarin", dose=dose, unit=unit)
