from app.models.experience import CandidateEvaluation, FailureFeedback


class FailureDiagnoser:
    def diagnose(self, evaluation: CandidateEvaluation) -> FailureFeedback:
        failed = [r for r in evaluation.results if r.passed is not True]
        return FailureFeedback(failed_tests=failed, weak_assumptions=[r.test_id for r in failed], repair_instructions=[f"Address {r.test_id}: {r.evidence}" for r in failed])
