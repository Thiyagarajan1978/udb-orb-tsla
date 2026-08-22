"""Static syntax guards for the Pine sources.

Pine cannot be compiled locally, so every paste is a live test on the user's chart. On 2026-08-20
a v3.9.17 tooltip shipped with an unescaped inner quote:

    tooltip="... justified OFF by "net-negative under the resting stop", which went stale ..."

The first inner quote CLOSED the string, so TradingView reported `Syntax error at input "net"` and
the whole script failed to compile.

The QC in use at the time counted quote PARITY per line. That bug adds TWO stray quotes, so parity
stayed even and the line read as clean -- a parity count can never see a balanced pair of strays,
and it also false-positives on any `//` comment that wraps a quoted phrase across two lines. This
tracks the actual string state instead.
"""

from pathlib import Path
import re

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PINE = [
    _ROOT / "pine" / "UDB_ORB_TSLA_v3.pine",
    _ROOT / "pine" / "UDB_ORB_TSLA_v3_strategy.pine",
]


def _scan_strings(text):
    """Walk each line tracking string state, honouring backslash escapes and `//` comments.

    Returns (lineno, message) for every line where a string fails to close, or closes immediately
    before a bare word -- the signature of a stray inner quote.
    """
    problems = []
    for lineno, line in enumerate(text.split("\n"), 1):
        i, in_str = 0, False
        while i < len(line):
            c = line[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == '"':
                    in_str = False
                    rest = line[i + 1:].lstrip()
                    if rest and (rest[0].isalnum() or rest[0] == "_"):
                        word = re.match(r"\w+", rest).group(0)
                        problems.append((lineno, f'string closes then bare word "{word}"'))
            elif c == '"':
                in_str = True
            elif line[i:i + 2] == "//":
                break
            i += 1
        if in_str:
            problems.append((lineno, "string not closed before end of line"))
    return problems


@pytest.mark.parametrize("path", _PINE, ids=lambda p: p.name)
def test_pine_string_literals_are_well_formed(path):
    """Catches both ways a Pine string has actually been broken in this repo.

    1. An unescaped inner quote (the v3.9.17 compile failure above) shows up as a string closing
       immediately before a bare word.
    2. A Git Bash heredoc eats backslashes even with a quoted delimiter, so a patch script written
       as `cat > f <<'EOF'` turns a tooltip's escaped newline into a REAL one and splits the string
       across lines. That shows up as "string not closed before end of line".

    Both have now happened, so they get a test rather than another note in CLAUDE.md.
    """
    problems = _scan_strings(path.read_text(encoding="utf-8"))
    assert not problems, "\n".join(f"{path.name}:{n}: {m}" for n, m in problems)
