# Large Change Workflow

Use for shared paths, contracts, schemas, architecture, or multi-domain changes.

1. Read harness and wiki context.
2. Use the runtime wiki graph capability or repository fallback exactly as
   described in `.harness/tooling-runtime.md` to discover related entities and
   source paths when scope crosses domains.
3. Inspect current code and tests.
4. Produce an implementation plan.
5. Produce test cases.
6. Get user confirmation.
7. Implement progressively.
8. Preserve backward compatibility unless explicitly approved.
9. Run planned verification.
10. Update wiki/harness when durable knowledge changes.

## Proportionate Performance And Recovery Planning

For each large change, describe the performance and resource impact,
concurrency model, crash/recovery behavior and validation cost at the level
needed by its actual risk. Do not mechanically require a numeric budget or a
benchmark when qualitative evidence is sufficient.

Classify planned evidence as:

- `qualitative` for ordinary or low-impact work with a clear bounded effect;
- `modeled` for potentially material impact supported by applicable history,
  complexity analysis, order-of-magnitude estimates or a bounded model; or
- `measured` when the design decision genuinely depends on full-volume scale, a
  core hot path, concurrency/recovery behavior or a high-cost control.

Do not present qualitative or modeled evidence as measured. A proposed full
scan, full replay, long transaction, global exclusive lock, repeated row-level
validation or comparable high-cost control must identify the concrete current
risk, its performance and recovery cost, and lower-cost alternatives. Evidence
collection must also be proportionate: do not default to a planning experiment
whose cost approaches implementation, and require a bounded probe or stronger
evidence only when unresolved uncertainty could materially change the design.
