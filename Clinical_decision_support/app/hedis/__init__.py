"""HEDIS engine: registry, supplemental submission, payer reconciliation, tiered agents and NCQA readiness.

Builds on the quality-loop measure engine (app.vbc_poc): its stored, versioned measure results are the
input; this package turns them into an authoritative member-measure registry and the payer-facing
workflows around it. State lives in its own file with the same locking and hash-chained audit as the POC.

Synthetic data and HEDIS-inspired prototype rules: readiness tooling, not an NCQA-certified product.
"""
