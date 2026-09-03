# Frontend Integration & API Contract Proposals

This document records observations, schema discrepancies, and proposed API enhancements identified during the React frontend integration. Per project constraints, the backend code remains frozen (with the exception of static-file SPA mounting and `/health` degraded verification). All recommended API evolutions are detailed below as proposals.

---

## 1. `/validate` Response Field Naming

### Discrepancy Observed
The prompt summary specified:
```json
{
  "valid": true,
  "missing_mandatory": [],
  "conditional": [],
  "present": [],
  "unrecognised": []
}
```

The actual implementation in `src/app/tools/validate_fields.py` (`FieldReport`) returns:
```json
{
  "valid": true,
  "checked": 55,
  "present": ["Supplier's Name", ...],
  "missing_mandatory": [
    {
      "no": 3,
      "name": "Supplier's TIN",
      "category": "Supplier's Details",
      "status": "mandatory",
      "condition": null,
      "section": "Guideline v4.8 §Appendix 1, p44"
    }
  ],
  "check_conditional": [
    {
      "no": 5,
      "name": "Supplier's SST Registration Number",
      "category": "Supplier's Details",
      "status": "conditional",
      "condition": "Mandatory for SST-registered persons",
      "section": "Guideline v4.8 §Appendix 1, p44"
    }
  ],
  "unknown_keys": []
}
```

### Differences:
1. `conditional` is named `check_conditional`.
2. `unrecognised` is named `unknown_keys`.
3. Elements in `missing_mandatory` and `check_conditional` are structured `FieldIssue` objects (`no`, `name`, `category`, `status`, `condition`, `section`) rather than raw field names or `{field, ref}` pairs.
4. `checked` is an integer reporting the total number of checked Appendix 1 fields (55).

### Proposal:
- Add property aliases on `FieldReport` in `src/app/tools/validate_fields.py` so both property names are supported:
  ```python
  @property
  def conditional(self) -> list[FieldIssue]:
      return self.check_conditional

  @property
  def unrecognised(self) -> list[str]:
      return self.unknown_keys
  ```
- The frontend was built against the actual schema (`check_conditional`, `unknown_keys`, `FieldIssue` objects) to leverage the rich guideline references (`Guideline v4.8 §Appendix 1, p44`).

---

## 2. Distinction Between 429 Quota Exhaustion & 429 Rate Limiting

### Observation
- **Token Quota Exhaustion**: Emitted by `src/app/api/main.py` when daily budget is spent (`budget.exhausted()`), returning HTTP 429 with `{ "error": "...", "resets_at": "2026-09-05T00:00:00+00:00", "budget": 100000 }`.
- **Throttling (Slowapi)**: Emitted by `@limiter.limit("10/minute")` when a client sends more than 10 requests per minute, returning HTTP 429 with `{ "error": "Too many requests. Please wait a moment and try again." }`.

### Proposal:
Add explicit machine-readable error codes to distinguish the two cases:
```json
// For daily quota:
{ "error_code": "QUOTA_EXHAUSTED", "resets_at": "...", "error": "..." }

// For burst rate limiting:
{ "error_code": "RATE_LIMITED", "retry_after": 60, "error": "..." }
```
In the frontend, we inspect the presence of `resets_at` to distinguish quota exhaustion (keeping invoice validation enabled and showing the exact reset time) from transient rate limiting ("Slow down, try again in a minute").

---

## 3. `/health` Status Verification for Guideline Versions

### Observation & Implementation:
Previously, `/health` returned `"status": "ok"` whenever the database connection succeeded, even if `latest_versions()` returned an empty tuple or raised an exception.

Per approved update:
- `/health` now computes:
  ```python
  "status": "ok" if db == "ok" and bool(versions) else "degraded"
  ```
- If `manifest.json` is missing or unreadable, `versions` remains empty and `/health` reports `"degraded"`, correctly reflecting that grounding documents cannot be verified.
- The frontend cold-start polling requires `status === "ok"` before activating the chat assistant.

---

## 4. Router Intent Classification Names

### Observation
The router emits the following intent strings:
- `general_qa`
- `applicability`
- `field_check`

### Proposal:
Standardize human-readable badges across API consumers. The frontend maps `general_qa` to **General**, `applicability` to **Applicability**, and `field_check` to **Field Check**.

