"""Static QC for Pine files: quote balance, paren/bracket balance, unresolved identifiers.

Pine cannot be compiled locally, so run this on EVERY Pine edit before pasting into
TradingView. It catches the mechanical breakages (a duplicated declaration line, an
unterminated string, a stray paren); it is BLIND to name collisions and type errors, so
still assume the first paste may fail.

    python scripts/pine_qc.py                    # the three SPX files (default)
    python scripts/pine_qc.py pine/OTHER.pine    # any file(s)

Known-benign "unresolved identifiers": named kwargs (textcolor, comment, max_labels_count,
default_qty_type, ...) and the 2nd+ variable on a multi-declaration line (`var float hi15 = na,
var float lo15 = na` -> lo15 flags). Judge the paren/bracket/quote lines, not that list.
"""
import re, sys

DEFAULT = ["pine/SPX_ORB_3BOT_v1_strategy.pine",
           "pine/SPX_ORB_3BOT_v1.pine",
           "pine/SPX_ORB_BOT3L_60M_v1_strategy.pine"]
files = sys.argv[1:] or DEFAULT

SQ = re.compile(r"'(?:\\.|[^'\\])*'")
DQ = re.compile(r'"(?:\\.|[^"\\])*"')

BUILTIN_PREFIX = re.compile(
    r"^(math|str|ta|input|plot|plotshape|plotchar|label|line|box|table|strategy|indicator|"
    r"alertcondition|color|display|size|shape|location|xloc|yloc|extend|runtime|array|matrix|map|"
    r"request|syminfo|timeframe|barstate|bar_index|time|close|open|high|low|volume|nz|na|hlc3|"
    r"ohlc4|and|or|not|if|else|for|while|var|varip|switch|true|false|int|float|bool|string|"
    r"timenow|year|month|dayofmonth|dayofweek|hour|minute|second|session|overlay|shorttitle|"
    r"title|group|tooltip|minval|maxval|step|options|defval|inline|confirm|display|force_overlay)$")

# member names that legitimately follow a dot on builtins
MEMBER = re.compile(
    r"^(new|get|set|delete|tostring|tonumber|round|max|min|abs|floor|ceil|sign|avg|sum|pow|sqrt|"
    r"log|exp|change|highest|lowest|sma|ema|rma|atr|tr|rsi|crossover|crossunder|valuewhen|"
    r"startswith|endswith|contains|split|replace|replace_all|format|length|pos|substring|upper|"
    r"lower|trim|isintraday|isdaily|isweekly|ismonthly|period|multiplier|isfirst|islast|"
    r"isconfirmed|isrealtime|ishistory|isnew|position_size|position_avg_price|entry|exit|close|"
    r"close_all|order|cancel|opentrades|closedtrades|netprofit|equity|error|fixed|percent_of_equity|"
    r"cash|long|short|data_window|none|all|pane|price|style_label_up|style_label_down|style_none|"
    r"style_linebr|style_line|style_stepline|style_circles|style_cross|style_columns|style_area|"
    r"style_labelup|style_labeldown|small|normal|tiny|large|huge|auto|white|black|gray|silver|red|"
    r"maroon|yellow|olive|lime|green|aqua|teal|blue|navy|fuchsia|purple|orange|security|"
    r"bar_index|abovebar|belowbar|absolute|triangleup|triangledown|labelup|labeldown|"
    r"style_triangleup|style_triangledown|style_arrowup|style_arrowdown|extend_none|"
    r"style_solid|style_dashed|style_dotted|top|bottom|left|right|center|cell|clear|"
    r"style_circle|now|int|float|bool|string|from|to)$")


def strip_line(l):
    s = SQ.sub("''", l)
    s = DQ.sub('""', s)
    return s.split("//")[0]


fail = 0
for f in files:
    src = open(f, encoding="utf-8").read()
    lines = src.split("\n")
    par = brk = 0
    defined = set()
    for i, l in enumerate(lines, 1):
        code = strip_line(l)
        if code.count("'") % 2 or code.count('"') % 2:
            print("%s:%d  UNBALANCED QUOTE" % (f, i))
            fail += 1
        par += code.count("(") - code.count(")")
        brk += code.count("[") - code.count("]")
        m = re.match(r"\s*(?:var\s+\w+\s+|varip\s+\w+\s+|var\s+)?([A-Za-z_]\w*)\s*(?::=|=)(?!=)", code)
        if m:
            defined.add(m.group(1))
        m2 = re.match(r"\s*([A-Za-z_]\w*)\s*\([^)]*\)\s*=>", code)
        if m2:
            defined.add(m2.group(1))
        for mm in re.finditer(r"\bfor\s+([A-Za-z_]\w*)", code):
            defined.add(mm.group(1))

    status = "OK" if par == 0 and brk == 0 else "MISMATCH"
    print("%-46s paren=%+d bracket=%+d  %s" % (f, par, brk, status))
    if par or brk:
        fail += 1

    code_all = "\n".join(strip_line(l) for l in lines)
    # drop dotted members: only check the ROOT identifier of a dotted chain
    roots = set()
    for mm in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)", code_all):
        roots.add(mm.group(1))
    unknown = sorted(r for r in roots
                     if r not in defined and not BUILTIN_PREFIX.match(r) and not MEMBER.match(r))
    if unknown:
        print("    unresolved identifiers:", unknown)
        fail += 1

print("\nQC", "FAILED" if fail else "PASSED")
sys.exit(1 if fail else 0)
