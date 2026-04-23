# ssh_argv fixtures

Language-agnostic contracts for `ashyterm/terminal/ssh_options.py`.

Each `*.json` describes one call:

```jsonc
{
  "description": "human note",
  "function": "build_base_ssh_options | build_ssh_test_options | apply_x11_and_tunnel_options",
  "session": { ...SessionItem kwargs... },
  "kwargs": { ...call kwargs... },
  "initial_options": { ... },        // for apply_* (pre-mutation dict)
  "expected_options": { ... },       // post-mutation / return value
  "expected_needs_x11": true|false   // optional — if present, assert needs_x11_flag
}
```

## Contract properties

- `function` names match the public API. Renaming them is a breaking change.
- `expected_options` is the **full** dict — no partial matches. Extra keys fail.
- `initial_options` keys that survive unchanged must appear in `expected_options`.

## Porting to Rust

The Rust implementation of `ssh_options` must satisfy every fixture here:
load the JSON, build a `SessionItem`-equivalent struct from `session`, call
the same function, assert output equals `expected_options`. No Python-specific
state (no logger calls, no module globals) is involved in these tests.
