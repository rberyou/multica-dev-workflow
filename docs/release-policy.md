# Release Policy

Release planning binds:

- VERSION, workflow and all Skill versions;
- exact merged PR head and merge commit;
- green validation run for the merge commit;
- tracked source hash and Changelog section;
- expected release assets;
- verified Maintenance Issue review evidence, or the one-time approved v6 bootstrap Plan for `v1.1.0-rc.1` only.

Required command sequence:

```text
python scripts/release.py plan --version <version> --maintenance-issue <T-ID>
APPROVE WORKFLOW RELEASE <short-digest>
python scripts/release.py approval-block --plan <file>
python scripts/release.py apply --plan <file> --approve <short-digest>
```

For normal releases, the durable human approver first posts the approval line on the Maintenance Issue. The read-only `approval-block` command then verifies that comment and prints a GitHub approval block containing the same digest, Maintenance Issue and Review comment IDs, Multica approval comment ID, and SHA256 commitments for the complete Maintenance evidence and approval author. The fixed workflow-product approver in `docs/bootstrap-v6.json` posts that exact block on the selected merged PR. Raw Multica member or Agent UUIDs are not written to Git. `apply` verifies both comments before creating a tag. For the one-time `v1.1.0-rc.1` bootstrap, the same record binds the already-approved Plan PR/comment and immutable approver login. `verify-tag` rechecks the exact GitHub approval block, tag commit, annotated source commit, exact merged-main PR and exact CI run so a hand-forged or stale tag cannot publish. The local `--approve` argument must match the same digest but does not replace durable approval records.

GitHub Release rejects tags without a release-plan annotation, exact merged-main PR provenance, matching versions or green CI. Publishing a Release does not authorize Multica Apply.
The annotated tag also carries the complete expected asset manifest and its SHA256 commitment. Release automation compares the built asset basenames with that exact set before publication and rejects missing or extra files.
