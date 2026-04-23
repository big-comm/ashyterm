# cli_parser corpus

Seed corpus for `CliArgParser.parse_command_line_args`. Two purposes:

1. **Regression**: every file under `valid/` and `edge/` is iterated by
   `tests/test_cli_parser_corpus.py`. New bugs get added here first
   (under `crashers/`) so they can never silently regress.
2. **Fuzz bootstrap**: when the Rust port lands, point `cargo fuzz`
   at this directory as the initial seed. The same inputs exercise the
   Rust parser; any mutation that crashes becomes a new `crashers/*.json`.

## File format

```jsonc
{
  "description": "human note",
  "argv": ["ashyterm", "-w", "/tmp"],
  "expected": {                       // optional — strict equality when present
    "working_directory": "/tmp",
    "execute_command": null,
    "ssh_target": null,
    "close_after_execute": false,
    "force_new_window": false
  },
  "assert_warning": true              // optional — logger.warning was called
}
```

If `expected` is omitted, the runner only asserts the parser did not
raise. Use that for entries where behaviour is "accepted but unchecked"
(current behaviour may not be ideal, but locking it in would be wrong).

## Layout

- `valid/` — normal usage. Every file has `expected`.
- `edge/` — odd inputs. Some have `expected`, some assert non-crash only.
- `crashers/` — empty for now. Add a file here when a bug is found, with
  the argv that reproduces it. The test runner picks it up automatically.

## Adding a case

Drop a `*.json` file in the matching subdir. The parametrized test
discovers it by filesystem walk; no code change needed.
