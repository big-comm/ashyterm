# ssh_config corpus

Paired fixtures for `ashyterm/utils/ssh_config_parser.py`:

```
NN_name.config          OpenSSH config file (literal)
NN_name.expected.json   { "description": "...", "expected": [SSHConfigHost dicts] }
```

Each `SSHConfigHost` dict has fields `alias`, `hostname`, `user`, `port`,
`identity_file`, `forward_x11` (values can be `null`).

The test runner `test_ssh_config_corpus.py` parses each `.config`, maps
the resulting `List[SSHConfigHost]` to dicts, and asserts equality with
the `expected` array. Order matters.

## Covered behaviours

- Single / multiple `Host` blocks.
- Multi-pattern `Host a b c` emits one entry per alias.
- Wildcards (`*`, `?`) and negations (`!`) are skipped.
- Case-insensitive keyword matching.
- Comments (`#`) and blank lines.
- Quoted values with spaces.
- `Match` terminates parsing (everything after ignored).
- Invalid `Port` value is logged and left `null` (no crash).
- Unknown keywords are stored but don't leak into the output struct.
- `ForwardX11` parses `yes`/`true`/`on` as True, else False.
- Empty file / file with only comments → no entries.

## Porting to Rust

The Rust equivalent of `SSHConfigParser.parse(path)` must produce the
exact `expected` array for every `.config` file here. This is a pure
parser; the fixtures port 1:1.

## Adding a case

Drop a `NN_name.config` + `NN_name.expected.json` pair. The test
discovers them by filesystem scan.
