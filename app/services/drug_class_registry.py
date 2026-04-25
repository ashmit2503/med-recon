from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Mapping


def _pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


DEFAULT_DRUG_CLASS_BY_NAME: dict[str, tuple[str, ...]] = {
    "acetaminophen": ("analgesic",),
    "ibuprofen": ("nsaid",),
    "naproxen": ("nsaid",),
    "aspirin": ("antiplatelet", "nsaid"),
    "warfarin": ("anticoagulant",),
    "apixaban": ("anticoagulant",),
    "lisinopril": ("ace_inhibitor",),
    "losartan": ("arb",),
    "spironolactone": ("potassium_sparing_diuretic",),
    "potassium chloride": ("potassium_supplement",),
    "metformin": ("biguanide",),
    "glipizide": ("sulfonylurea",),
    "atorvastatin": ("statin",),
    "simvastatin": ("statin",),
}

DEFAULT_CONFLICTING_CLASS_COMBINATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        _pair("ace_inhibitor", "arb"),
        _pair("anticoagulant", "antiplatelet"),
        _pair("anticoagulant", "nsaid"),
        _pair("potassium_sparing_diuretic", "potassium_supplement"),
        _pair("sulfonylurea", "insulin"),
    }
)


@dataclass(frozen=True)
class DrugClassRegistry:
    drug_class_by_name: Mapping[str, tuple[str, ...]]
    conflicting_class_combinations: frozenset[tuple[str, str]]

    def get_classes_for_drug(self, canonical_name: str) -> tuple[str, ...]:
        return self.drug_class_by_name.get(canonical_name, ())

    def get_classes_for_drugs(self, canonical_names: Iterable[str]) -> set[str]:
        classes: set[str] = set()
        for name in canonical_names:
            classes.update(self.get_classes_for_drug(name))

        return classes

    def find_conflicting_pairs(self, classes: Iterable[str]) -> list[tuple[str, str]]:
        class_set = sorted(set(classes))
        conflicts: list[tuple[str, str]] = []
        for left, right in combinations(class_set, 2):
            pair = _pair(left, right)
            if pair in self.conflicting_class_combinations:
                conflicts.append(pair)

        return conflicts


DEFAULT_DRUG_CLASS_REGISTRY = DrugClassRegistry(
    drug_class_by_name=DEFAULT_DRUG_CLASS_BY_NAME,
    conflicting_class_combinations=DEFAULT_CONFLICTING_CLASS_COMBINATIONS,
)
