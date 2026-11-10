# ADR-003: Placeholder grammar

Status: accepted - 2026-11-06. Implementation: `shared/rp_shared/placeholders.py`.

```
{{ key }}                          value
{{ key | op1 | op2:arg }}          operations, left to right
{{ #each rows }} ... {{ /each }}   loop ({{ col }} or {{ this.col }} inside)
{{ #if key }} ... {{ else }} ... {{ /if }}
```

Operations: `date:fmt`, `upper`, `lower`, `multiply:n`, `round:n`, `suffix:str`, `prefix:str`,
`default:str`, `number`, `raw`. (`lookup` arrives with the ingestion service.)

## Rules (each one comes from a desktop-app bug or a safety need)
- CRLF -> LF before parsing.
- Operations strictly left to right; `round` is half-up (Excel), not banker's rounding.
- Key absent from the context = **unresolved** and reported; key present but empty = empty.
- Output HTML-escaped unless the chain contains `raw`.
- Only the first `:` splits an operation from its argument (`date:%H:%M` works).
  Arguments keep leading spaces (`suffix: kg`) but cannot end with a space, because the tag
  body is trimmed.
- No code execution: the engine is a closed set of operations.
- Excel headers are normalised to snake_case keys at import (`nom entreprise client` ->
  `nom_entreprise_client`); the original header is kept as the label.
