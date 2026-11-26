from app.models.experience import CandidateEvaluation


class CandidateSelector:
    """Mode-specific selection; runtime never ranks by diagnostic correctness."""

    @staticmethod
    def select_controlled(candidates: list[CandidateEvaluation]) -> CandidateEvaluation | None:
        eligible = [item for item in candidates if item.mode == "controlled"]
        return max(eligible, key=lambda item: (
            item.diagnosis_correct is True,
            item.action_quality is True,
            item.evidence_consistent is True,
        ), default=None)

    @staticmethod
    def select_runtime(candidates: list[CandidateEvaluation]) -> CandidateEvaluation | None:
        eligible = [item for item in candidates
                    if item.mode == "runtime" and item.schema_valid and not item.repair_reasons]
        return max(eligible, key=lambda item: (
            item.schema_valid,
            not item.repair_reasons,
            item.evidence_consistent is True,
            item.action_quality is True,
        ), default=None)

    @staticmethod
    def select(candidates: list[CandidateEvaluation]) -> CandidateEvaluation | None:
        """Backward-compatible dispatcher; mixed modes are rejected."""
        if not candidates:
            return None
        modes = {item.mode for item in candidates}
        if len(modes) != 1:
            raise ValueError("Candidates from controlled and runtime modes cannot be compared.")
        return (CandidateSelector.select_controlled(candidates) if "controlled" in modes
                else CandidateSelector.select_runtime(candidates))
