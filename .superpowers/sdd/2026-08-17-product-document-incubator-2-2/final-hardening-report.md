# Product Document Incubator 2.2 Final Security Hardening

## Outcome

Status: DONE_WITH_CONCERNS

Commit: `f2e42d6 fix: harden wiki security boundaries`

## Findings addressed

1. **L3/L4 and malformed source-page citations**
   - The full source page is now parsed for complete, canonical citation tokens.
   - Every cited source must be the current project and externally exportable; deterministic redaction is re-run over the actual page before it becomes document-model context.
   - Any unsafe, cross-project, ambiguous, cross-line, or residual-sensitive citation fails closed before Gateway invocation.

2. **Role-directory symlink redirects**
   - `raw/`, `wiki/`, `schema/`, `exports/`, and `.incubator/` must remain canonical lexical directories.
   - Project resolution, archive writes, and transaction boundaries re-check this condition, preventing `raw -> wiki/current` redirects.

3. **Raw integrity across transaction and recovery boundaries**
   - Raw relative path, SHA-256, and byte length are persisted in the content-free transaction journal.
   - Evidence is checked before staging, before database commit, after database commit, after file verification, during rollback, and during recovery.
   - A changed or redirected Raw produces `recovery_required`; it cannot produce an ingested success.

4. **Central permission authority**
   - External Wiki Ingest and document-context projection use the current central SQLite `projects.allow_external_model` value immediately before outbound authorization.
   - Local `project.json` metadata cannot elevate a revoked project.

5. **Multi-source citation ownership**
   - Topic output citations preserve every contributing source ID.
   - Existing source locators are retained; new source locators are derived from the trusted source Raw, so one selected source cannot be stamped onto another source's evidence.

6. **Excluded sensitive-topic visibility**
   - Exclusion count and a content-free local-only comparison notice are persisted in source-index/Wiki evidence-gap output and surfaced in Materials UI.
   - Excluded title/body text is never retained in outbound projection or the warning.

7. **Content-free external Wiki audit**
   - Wiki calls record task type, source IDs, outbound character count, authorization/redaction state, and success/failure outcome through the existing model-call ledger.
   - Bodies, Raw bytes, and secrets are not written to the audit record.

8. **UI compatibility**
   - Removed the literal `\\n+` rendering artifact.
   - Already-ingested Wiki results render without an active gateway credential; only new/retry actions require the credential.

## Verification

- Focused security/integration/UI tests: passed.
- Full suite: `1046 passed`.
- Ruff on all changed production and test files: passed.
- `python -m compileall -q src tests`: passed.
- `git diff --check`: passed.

## Residual concerns

- The repository retains previously accepted full-suite coverage/format debt outside this hardening scope. The functional full suite is green; those historical quality gates remain an explicitly separate governance task.
- `WikiChangeSet` keeps sentinel defaults for old pure-domain constructors. The transaction coordinator rejects sentinel evidence before any write, while all production use cases populate the trusted Raw evidence explicitly.

## Fix round 1

- Date: 2026-08-19
- Scope: final-review wave for the remaining five hardening findings after `f2e42d6`.

### Additional fixes completed

1. Structural symlink rejection now fails closed before discovery, projection, authorization, or outbound invocation.
   - `wiki/topics -> wiki/current` and other nested role redirects are rejected through canonical component checks.
   - External Wiki Ingest records zero gateway calls when this preflight fails.

2. Recovery no longer trusts project-local journal contents by themselves.
   - A trusted transaction binding digest is persisted in central SQLite before file staging.
   - Recovery compares journal `source_id`, Raw evidence, and governed targets against the trusted binding before restore or DB mutation.
   - Any mismatch moves the transaction to `recovery_required`.

3. Outbound topic projection now requires every cited related-topic source to already be `ingested`.
   - Pending or failed sources exclude the whole topic from outbound context.
   - The active source being ingested remains authorizable for its own outbound payload, but it cannot retroactively authorize unrelated pending evidence.

4. Model-returned Markdown citations are now strictly validated before any Wiki mutation.
   - `source_page_markdown` and every `topic.markdown` must contain only complete, unambiguous citations.
   - Cited source IDs must belong to the current project and to the trusted `source_ids` set for that block.
   - Invalid injected citations fail before transaction assembly and leave Wiki files unchanged.

5. Wiki audit records now reflect actual preflight state.
   - Permission denial and outbound safety-proof failure produce content-free audit rows with `local_only` result mode and zero gateway calls.
   - Successful and failed external calls record real authorization, redaction, and invocation state.
   - Audit logger write failures are swallowed so they cannot mask the original business error.

### Verification completed

- Focused regressions: `66 passed`
- Full suite: `1060 passed`
- Ruff on changed files: passed
- `../../.venv/bin/python -m compileall -q src tests`: passed
- `git diff --check`: passed

### Remaining concern

- `project_library.py` contains canonical path hardening required by this wave and is included with the final security commit; no broader repository hygiene work was performed beyond this scope.
