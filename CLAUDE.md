# SPAS Backend Instructions

[AGENTS.md](AGENTS.md) is the canonical engineering guide for this repository.
Read it before changing Django models, serializers, permissions, querysets,
migrations, fixtures, or tests.

In particular:

- enforce permissions and row authority on reads and writes;
- preserve tenant isolation, soft archives, actor fields, and history snapshots;
- treat `SubjectAllocation` as the class and `SubjectEnrollment` as its roster;
- allow attendance mutations only in running semesters and valid date bounds;
- use transactional writes, constraints, stable validation errors, and
  regression tests for cross-scope IDs;
- run migration drift, Django, Ruff, and pytest checks before handoff.

This file intentionally contains no frontend conventions. Frontend guidance
belongs in the frontend repository.
