# Knowledge Sync Workflow

Use this before handing off a meaningful wiki or harness change.

1. Check changed entities against `.project-wiki/schema.md` and the affected sources.
2. Check that important claims have a source entry or are listed as open questions.
   Verify confirmed requirements, observations, proposals and open questions remain
   distinguishable; preserve raw evidence in `.project-wiki/raw/` and retain
   confirmation boundaries.
3. Keep each full rule in its owning topic. Check affected dependency links without
   requiring every unrelated page to be read or rewritten.
4. Ensure the short index routes to new or moved pages. Check the project human
   reading guide when human-facing document routes or purposes change. Change root
   entries only when agent-wide rules or routes change, not for ordinary business
   confirmations.
5. Check that `.project-wiki/log/` contains a concise summary and owning-topic
   links, one file per change, and that Harness changes also have an entry in
   `.harness/update-log/`. Both use `YYYY-MM-DD-identifier.md`, with only lowercase
   letters, digits and hyphens in the identifier; use `issue-N-short-description`
   when there is an Issue. Read entries in filename order. Keep the old
   `.project-wiki/log.md` and `.harness/update-log.md` unchanged; do not append.
6. For document moves, verify old-to-new coverage, content preservation and local
   links. Report actual checks and unresolved concerns, including open
   questions; do not claim product acceptance from document or harness
   validation.
