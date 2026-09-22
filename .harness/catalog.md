# Harness Catalog

| Task | Workflow | Knowledge |
| --- | --- | --- |
| New repository before its first remote | [Local bootstrap](workflows/local-bootstrap.md) | [Getting started](../docs/getting-started.md) |
| SQL APM requirements discussion before first remote | Root rules and [knowledge sync](workflows/knowledge-sync.md) | [Project knowledge](../.project-wiki/index.md), [local scope](plans/local-bootstrap.md) |
| Tool discovery or diagnosis | [Tooling runtime](tooling-runtime.md) | [Tool contract](../docs/runbooks/issue-pr-quality-tooling.md) |
| Copy or string edit | Root rules, then target file | Target file |
| Small local change | [Small change](workflows/small-change.md) | Relevant wiki entity |
| Large or cross-domain change | [Large change](workflows/large-change.md) | Architecture, contracts and decisions |
| Requirements, Issues or PR delivery after connection | [GitHub planning](workflows/github-planning.md) | Live contract and accepted project knowledge |
| Review or handoff | [Review](workflows/review-sync.md), [knowledge sync](workflows/knowledge-sync.md) | Affected entities |
| Wiki maintenance | [Wiki update](workflows/wiki-update.md) | [Schema](../.project-wiki/schema.md) |
| Adopt a harness update | [Updating](../docs/updating.md) | [Provenance](../docs/provenance.md) |

Projects add their domain mappings here. Resolve uncertain scope through the
optional capability discovery or repository search in the runtime guide.
