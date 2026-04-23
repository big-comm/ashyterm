# bash_markup snapshots

Frozen output of `ashyterm.utils.syntax_utils.get_bash_pango_markup` for
a curated set of bash command inputs. `snapshots.json` maps a case name
to `{input, expected_markup}`.

Any change in the highlighter (pattern order, color palette, escaping)
surfaces as a test failure pinpointing exactly which input changed.

## Regeneration

When an intentional change lands:

```bash
python -c "
import json, sys, os
sys.path.insert(0, 'src')
from unittest.mock import MagicMock
sys.modules['gi'] = MagicMock()
sys.modules['gi.repository'] = sys.modules['gi'].repository
from ashyterm.utils.syntax_utils import get_bash_pango_markup
with open('tests/fixtures/bash_markup/snapshots.json') as f:
    data = json.load(f)
for k, v in data.items():
    v['expected_markup'] = get_bash_pango_markup(v['input'])
with open('tests/fixtures/bash_markup/snapshots.json', 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
"
```

Then review the diff carefully before committing — the snapshot *is*
the contract.

## Porting to Rust

If the Rust port keeps Pango markup (vs GTK `Label::set_markup_attributes`
or custom renderer), these fixtures apply 1:1. If the renderer changes,
snapshots become a golden for the new output format and still carry
their value.
