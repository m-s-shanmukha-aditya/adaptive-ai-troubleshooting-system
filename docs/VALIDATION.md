# Runtime validation

The runtime validator checks the generated candidate's structure and consistency with the extracted observations and selected troubleshooting knowledge. It does not decide whether a diagnosis is true.

## Validation flow

1. Parse the candidate as one JSON object matching the structured-candidate schema. The schema requires version `1`, a diagnosis with an allowed status, and correctly shaped evidence, actions, resolution claim, and follow-up questions. Unknown fields are rejected. A parse or schema failure emits `malformed_or_invalid_schema` and ends validation.
2. Check that the diagnosis ID appears in the selected blueprint (or in the built-in IDs when no blueprint definitions are supplied). An unknown ID emits `unknown_diagnosis_id`.
3. Check that every cited evidence ID is present in the extracted observations. A missing ID emits `invalid_evidence_references`.
4. Check that every action ID is in the blueprint's action set (or the built-in action set when no blueprint actions are supplied) and that no action ID or description contains a prohibited unsafe phrase. A failure emits `unsafe_or_unknown_action`.
5. For a `supported` diagnosis, check that the cited evidence contains at least one of the blueprint's required supporting evidence IDs. If it does not, or the diagnosis defines no required evidence, emit `diagnosis_not_supported_by_cited_observations`.
6. When the blueprint defines action-to-diagnosis links and the selected diagnosis is known, check that diagnosis's own allowed-action list. If any recommended action is not linked to that diagnosis, emit `action_not_allowed_for_diagnosis`. If actions were supplied but none are linked to that diagnosis, also emit `actions_not_relevant_to_diagnosis`.
7. Check that the resolution claim is `unverified`, `not_verified`, or `unknown`. Any other value emits `unsupported_resolution_claim`.
8. Reject percentage or numeric probability/confidence claims in the candidate text. Such a claim emits `quantified_confidence_not_allowed`.

All applicable failures are returned together, except that invalid JSON or a schema violation ends validation before the remaining checks.

## Example that passes

Given the extracted observation ID `service_state`, this candidate passes when the PostgreSQL blueprint is selected:

```json
{
  "schema_version": "1",
  "diagnosis": {
    "id": "postgres_service_stopped",
    "summary": "The reported service state is stopped.",
    "status": "supported"
  },
  "evidence": [{"observation_id": "service_state"}],
  "recommended_actions": [{"action_id": "verify_service_status"}],
  "resolution_claim": "unverified",
  "follow_up_questions": []
}
```

## Example that fails

This candidate cites an existing observation, but not evidence required for the selected supported diagnosis. It emits `diagnosis_not_supported_by_cited_observations`, a failure code seen in the demo's s2 runs.

```json
{
  "schema_version": "1",
  "diagnosis": {
    "id": "datasource_port_incorrect",
    "summary": "The reported service state is stopped.",
    "status": "supported"
  },
  "evidence": [{"observation_id": "service_state"}],
  "recommended_actions": [{"action_id": "align_datasource_port"}],
  "resolution_claim": "unverified",
  "follow_up_questions": []
}
```

## What this does not prove

Passing validation means the candidate is structurally valid and consistent with the extracted facts and selected blueprint. Those extracted facts are claims from the user's report, not independently verified truth. Passing does not prove that the diagnosis is correct, that an action will work, or that the issue has been resolved.
