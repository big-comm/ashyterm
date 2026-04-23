# json_migrations fixtures

Pairs of `*.before.json` + `*.after.json` for each schema migration in
the project. The `after` is the exact byte-equivalent of what
`migrate_data(before, target_version, migrations)` must produce.

## Structure

```
json_migrations/
  window_state/
    v1_*.before.json / v1_*.after.json   # migrate v1 → current
    v2_passthrough.*                       # already-current = identity
```

## Porting to Rust

Each migration function has an implementation-language-specific body.
The **contract** — input byte-for-byte → output byte-for-byte — is
language-agnostic. A Rust port of `WindowStateManager::migrate` (or
equivalent) must produce the declared `after.json` for every
`before.json`.

## Adding a migration

1. Implement `_migrate_vN_to_vN1` in the real code.
2. Bump `SCHEMA_VERSION`.
3. Drop a `vN_*.before.json` + matching `after.json` pair here.
4. Tests in `test_json_versioning.py` pick them up by filesystem scan.
