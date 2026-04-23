# backup fixtures — known-answer tests

Two contracts for `ashyterm/utils/backup.py`:

## 1. `vectors.json` — roundtrip table

10 vectors of `(password, files)`. For each vector:

1. Write the declared files to a temp dir.
2. Call `BackupManager.create_encrypted_backup(...)` with the password.
3. Call `BackupManager.restore_from_encrypted_backup(...)` into a second temp dir.
4. Assert every declared file matches byte-for-byte.

This catches regressions where compression, password handling, or file
layout quietly change. The Rust port is expected to satisfy the same table.

## 2. `known_v1.7z` + `known_v1_manifest.json` — cross-implementation vector

A pre-generated encrypted 7z archive, committed to the repo. Any
implementation (current Python, Rust port, future rewrite) must be able
to decrypt this exact file with the manifest's password and extract the
files named in `expected_files`.

**Do not regenerate** this file casually — its value is that it's a
stable artefact frozen at the time of commit. Only regenerate when the
archive format itself is intentionally changed, and bump the `v1` suffix
(e.g. `known_v2.7z`).

### Regeneration recipe (if ever needed)

```python
import py7zr, tempfile, pathlib
d = pathlib.Path(tempfile.mkdtemp())
(d / 'sessions.json').write_text('[]')
(d / 'folders.json').write_text('[]')
(d / 'layouts').mkdir()
(d / 'layouts' / 'default.json').write_text('{"name":"default"}')
with py7zr.SevenZipFile('known_v1.7z', 'w', password='kat-test-password-v1') as a:
    a.writeall(d, arcname='')
```
