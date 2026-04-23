# osc7 corpus

`ashyterm/utils/osc7.py :: parse_directory_uri` — live CWD tracking.

Each entry:

```jsonc
{
  "description": "...",
  "uri": "file://...",
  "expected": { "hostname": "...", "path": "..." } | null,
  "expected_display_path": "/etc/ssh",               // optional, exact match
  "expected_display_path_contains": ".../e/f/g",     // optional, substring
  "accept_any_result": true                          // optional, non-crash only
}
```

`null` means the parser must return `None`. Otherwise the returned
`OSC7Info.hostname` + `path` must match. `display_path` checks are
optional because shortening depends on `$HOME` at runtime.

## Porting to Rust

`parse_directory_uri` is a pure function: `&str -> Option<OSC7Info>`.
These fixtures are the contract. In Rust, URL parsing lives in the
`url` crate — percent decoding behaves identically for valid inputs,
but the edge cases (`%Z`, null bytes, bare fragments) may differ.
That's why we pin the behaviour here before porting.
