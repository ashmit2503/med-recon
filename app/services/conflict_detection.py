from collections import defaultdict
from dataclasses import dataclass
import hashlib
import re

from pydantic import ValidationError

from app.models.conflict import (
    ConflictDocument,
    ConflictEvidence,
    ConflictMedicationRef,
    ConflictSeverity,
    ConflictType,
)
from app.models.medication_snapshot import (
    MedicationItem,
    MedicationSnapshotDocument,
    MedicationSource,
    MedicationStatus,
)
from app.services.drug_class_registry import (
    DEFAULT_DRUG_CLASS_REGISTRY,
    DrugClassRegistry,
)
from app.services.medication_normalization import (
    MedicationNormalizationService,
    NormalizedMedication,
)

_KEY_SAFE_PATTERN = re.compile(r"[^a-z0-9:_-]+")


@dataclass(frozen=True)
class MedicationObservation:
    medication: MedicationItem
    source: MedicationSource
    source_version: str
    identifier: str
    canonical_name: str
    canonical_dose: str | None
    drug_classes: tuple[str, ...]
    status: MedicationStatus


class ConflictDetectionEngine:
    def __init__(
        self,
        registry: DrugClassRegistry = DEFAULT_DRUG_CLASS_REGISTRY,
        normalizer: MedicationNormalizationService | None = None,
    ) -> None:
        self.registry = registry
        self.normalizer = normalizer or MedicationNormalizationService(registry=registry)

    def detect_conflicts(
        self,
        snapshot: MedicationSnapshotDocument,
    ) -> list[ConflictDocument]:
        if len(snapshot.source_versions) < 2:
            return []

        observations = self._collect_observations(snapshot)
        if not observations:
            return []

        conflicts: list[ConflictDocument] = []
        conflicts.extend(self._detect_dose_mismatches(snapshot, observations))
        conflicts.extend(self._detect_class_combination_conflicts(snapshot, observations))
        conflicts.extend(self._detect_status_disagreements(snapshot, observations))

        return sorted(conflicts, key=lambda conflict: conflict.conflict_key)

    def _collect_observations(
        self,
        snapshot: MedicationSnapshotDocument,
    ) -> list[MedicationObservation]:
        observations: list[MedicationObservation] = []

        for source_version in snapshot.source_versions:
            for medication in source_version.medications:
                normalized = self._safe_normalize_medication(medication)
                if normalized is None:
                    continue

                observations.append(
                    MedicationObservation(
                        medication=medication,
                        source=source_version.source,
                        source_version=source_version.version,
                        identifier=self._build_medication_identifier(medication, normalized),
                        canonical_name=normalized.canonical_name,
                        canonical_dose=normalized.canonical_dose,
                        drug_classes=normalized.drug_classes,
                        status=medication.status,
                    )
                )

        return observations

    def _safe_normalize_medication(
        self,
        medication: MedicationItem,
    ) -> NormalizedMedication | None:
        raw_name = medication.name.strip()
        if not raw_name:
            return None

        try:
            return self.normalizer.normalize(raw_name, dose=medication.dose)
        except (ValidationError, ValueError):
            try:
                return self.normalizer.normalize(raw_name)
            except (ValidationError, ValueError):
                return None

    def _build_medication_identifier(
        self,
        medication: MedicationItem,
        normalized: NormalizedMedication,
    ) -> str:
        if medication.rxnorm_code:
            return f"rxnorm:{medication.rxnorm_code.strip().lower()}"

        return f"name:{normalized.canonical_name}"

    def _group_observations_by_medication(
        self,
        observations: list[MedicationObservation],
    ) -> dict[str, list[MedicationObservation]]:
        grouped: dict[str, list[MedicationObservation]] = defaultdict(list)
        for observation in observations:
            grouped[observation.identifier].append(observation)

        return grouped

    def _detect_dose_mismatches(
        self,
        snapshot: MedicationSnapshotDocument,
        observations: list[MedicationObservation],
    ) -> list[ConflictDocument]:
        conflicts: list[ConflictDocument] = []
        grouped = self._group_observations_by_medication(observations)

        for medication_key, medication_observations in grouped.items():
            active_observations = [
                observation
                for observation in medication_observations
                if observation.status == MedicationStatus.ACTIVE
            ]
            if len({observation.source for observation in active_observations}) < 2:
                continue

            observations_with_dose = [
                observation
                for observation in active_observations
                if observation.canonical_dose is not None
            ]
            if len(observations_with_dose) < 2:
                continue

            distinct_doses = sorted(
                {
                    observation.canonical_dose
                    for observation in observations_with_dose
                    if observation.canonical_dose is not None
                }
            )
            if len(distinct_doses) < 2:
                continue

            medication_name = observations_with_dose[0].canonical_name
            dose_rollup = ", ".join(
                f"{observation.source.value}={observation.canonical_dose}"
                for observation in observations_with_dose
            )

            conflicts.append(
                ConflictDocument(
                    patient_id=snapshot.patient_id,
                    snapshot_id=snapshot.id,
                    conflict_key=self._build_conflict_key(
                        snapshot.id,
                        ConflictType.DOSAGE_MISMATCH,
                        medication_key,
                    ),
                    conflict_type=ConflictType.DOSAGE_MISMATCH,
                    severity=ConflictSeverity.HIGH,
                    title=f"Dose mismatch for {medication_name}",
                    description=(
                        f"Active medication '{medication_name}' has inconsistent doses "
                        f"across sources: {dose_rollup}."
                    ),
                    medications=self._build_medication_refs(observations_with_dose),
                    evidence=[
                        ConflictEvidence(
                            source=observation.source,
                            source_version=observation.source_version,
                            detail=(
                                f"{observation.canonical_name} reported as "
                                f"{observation.canonical_dose}."
                            ),
                        )
                        for observation in observations_with_dose
                    ],
                    recommended_action=(
                        "Confirm intended dose with prescribing source and reconcile "
                        "all active records."
                    ),
                    metadata={
                        "medication_key": medication_key,
                        "dose_values": "|".join(distinct_doses),
                    },
                )
            )

        return conflicts

    def _detect_class_combination_conflicts(
        self,
        snapshot: MedicationSnapshotDocument,
        observations: list[MedicationObservation],
    ) -> list[ConflictDocument]:
        conflicts: list[ConflictDocument] = []
        active_observations = [
            observation
            for observation in observations
            if observation.status == MedicationStatus.ACTIVE
        ]
        if len({observation.source for observation in active_observations}) < 2:
            return conflicts

        active_classes = {
            drug_class
            for observation in active_observations
            for drug_class in observation.drug_classes
        }
        for left_class, right_class in self.registry.find_conflicting_pairs(active_classes):
            involved = [
                observation
                for observation in active_observations
                if left_class in observation.drug_classes
                or right_class in observation.drug_classes
            ]
            if len({observation.identifier for observation in involved}) < 2:
                continue

            class_pair = f"{left_class}+{right_class}"
            conflicts.append(
                ConflictDocument(
                    patient_id=snapshot.patient_id,
                    snapshot_id=snapshot.id,
                    conflict_key=self._build_conflict_key(
                        snapshot.id,
                        ConflictType.DRUG_INTERACTION,
                        class_pair,
                    ),
                    conflict_type=ConflictType.DRUG_INTERACTION,
                    severity=ConflictSeverity.HIGH,
                    title=f"Dangerous class combination: {left_class} + {right_class}",
                    description=(
                        "Detected active medications with known conflicting classes "
                        f"({left_class} and {right_class}) across source snapshots."
                    ),
                    medications=self._build_medication_refs(involved),
                    evidence=[
                        ConflictEvidence(
                            source=observation.source,
                            source_version=observation.source_version,
                            detail=(
                                f"{observation.canonical_name} contributes classes "
                                f"{','.join(observation.drug_classes)}."
                            ),
                        )
                        for observation in involved
                    ],
                    recommended_action=(
                        "Validate indication overlap and assess whether regimen "
                        "modification is required."
                    ),
                    metadata={"class_pair": class_pair},
                )
            )

        return conflicts

    def _detect_status_disagreements(
        self,
        snapshot: MedicationSnapshotDocument,
        observations: list[MedicationObservation],
    ) -> list[ConflictDocument]:
        conflicts: list[ConflictDocument] = []
        grouped = self._group_observations_by_medication(observations)

        for medication_key, medication_observations in grouped.items():
            active_observations = [
                observation
                for observation in medication_observations
                if observation.status == MedicationStatus.ACTIVE
            ]
            stopped_observations = [
                observation
                for observation in medication_observations
                if observation.status == MedicationStatus.DISCONTINUED
            ]

            if not active_observations or not stopped_observations:
                continue

            combined_observations = active_observations + stopped_observations
            if len({observation.source for observation in combined_observations}) < 2:
                continue

            medication_name = combined_observations[0].canonical_name
            conflicts.append(
                ConflictDocument(
                    patient_id=snapshot.patient_id,
                    snapshot_id=snapshot.id,
                    conflict_key=self._build_conflict_key(
                        snapshot.id,
                        ConflictType.SOURCE_DISAGREEMENT,
                        medication_key,
                    ),
                    conflict_type=ConflictType.SOURCE_DISAGREEMENT,
                    severity=ConflictSeverity.MODERATE,
                    title=f"Source status disagreement for {medication_name}",
                    description=(
                        f"Medication '{medication_name}' is marked discontinued in at "
                        "least one source while remaining active in another source."
                    ),
                    medications=self._build_medication_refs(combined_observations),
                    evidence=[
                        ConflictEvidence(
                            source=observation.source,
                            source_version=observation.source_version,
                            detail=(
                                f"{observation.canonical_name} status is "
                                f"{observation.status.value}."
                            ),
                        )
                        for observation in combined_observations
                    ],
                    recommended_action=(
                        "Resolve source-of-truth for medication status before finalizing "
                        "the reconciled list."
                    ),
                    metadata={
                        "medication_key": medication_key,
                        "active_sources": str(len(active_observations)),
                        "stopped_sources": str(len(stopped_observations)),
                    },
                )
            )

        return conflicts

    def _build_medication_refs(
        self,
        observations: list[MedicationObservation],
    ) -> list[ConflictMedicationRef]:
        references: list[ConflictMedicationRef] = []
        seen: set[tuple[str, str, str]] = set()

        for observation in observations:
            dedupe_key = (
                observation.medication.medication_id,
                observation.source.value,
                observation.source_version,
            )
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)
            references.append(
                ConflictMedicationRef(
                    medication_id=observation.medication.medication_id,
                    name=observation.canonical_name,
                    rxnorm_code=observation.medication.rxnorm_code,
                    source=observation.source,
                    source_version=observation.source_version,
                )
            )

        return references

    def _build_conflict_key(
        self,
        snapshot_id: str,
        conflict_type: ConflictType,
        anchor: str,
    ) -> str:
        raw_key = f"{snapshot_id}:{conflict_type.value}:{anchor}".lower()
        sanitized = _KEY_SAFE_PATTERN.sub("-", raw_key).strip("-")

        if len(sanitized) <= 160:
            return sanitized

        digest = hashlib.sha1(sanitized.encode("utf-8")).hexdigest()[:12]
        prefix = sanitized[:147].rstrip("-")
        return f"{prefix}:{digest}"[:160]
