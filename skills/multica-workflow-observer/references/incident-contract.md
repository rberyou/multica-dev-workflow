# Incident Contract

Report workflow-rule conflicts, invalid approvals/reviews/dependencies, missing required roles or metadata, platform-assumption mismatches, repeated workflow-level failure, managed drift, duplicate or orphan Issues.

Do not report ordinary code defects, failed product tests, unclear business requirements, one transient Runtime failure, or a Plan defect already following the normal Plan revision path.

| Severity | Meaning | Source behavior |
|---|---|---|
| urgent | invalid approval, security/privacy exposure, destructive Git history | block immediately |
| high | workflow may merge or complete incorrect work | block affected stage |
| medium | recoverable stall, duplicate work or process violation | report; continue only if correctness is intact |
| low | documentation, usability or efficiency gap | report and continue |

Incident metadata is routing data. Put reproduction and evidence in the description/comments after redaction. Never copy tokens, cookies, credentials, private keys, raw environment values or unredacted user content.
