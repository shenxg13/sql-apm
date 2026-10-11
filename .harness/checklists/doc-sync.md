# Doc Sync Checklist

- [ ] Entry files route to `.harness/index.md`.
- [ ] Relevant wiki pages are updated or confirmed unchanged.
- [ ] Historical docs were not treated as current without verification.
- [ ] Verification was run or skipped with reason.
- [ ] Full rules have one owning topic; entry/index changes are limited to routes and shared workflow.
- [ ] Moved knowledge retains confirmed details, sources, open questions and valid links.
- [ ] New knowledge summaries and owning-topic links are in `.project-wiki/log/`, one file per change; Harness changes also have one entry per change in `.harness/update-log/`.
- [ ] Both directories use `YYYY-MM-DD-identifier.md`, where the identifier contains only lowercase letters, digits and hyphens, and starts with `issue-N-` plus a short description when there is an Issue; entries are read in filename order.
- [ ] Existing entries in `.project-wiki/log.md` and `.harness/update-log.md` remain frozen; neither file receives new entries.
