"""Regex symbol extraction for JS/TS, Go, Java, Rust, PHP, Ruby, Dart, Kotlin and C# (A5.1).

No parser: strings and comments are blanked first (`mask`), so a "{" or "class" inside them never
counts; then each line is matched against the start patterns of its language, and the end of a
symbol is found by scanning its header to the body ("{" … matching "}", or "=", "=>", ";").
Ruby has no braces: its blocks are counted by keyword (def/class/module/do/if… … end).
"""
from __future__ import annotations

import re

LANG_BY_EXT = {
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts",
    ".go": "go", ".java": "java", ".rs": "rust", ".php": "php", ".rb": "ruby", ".rake": "ruby",
    ".dart": "dart", ".kt": "kotlin", ".kts": "kotlin", ".cs": "cs",
}
MAX_DECL_CHARS = 300  # declarations start a line; never run the patterns over a 50k-char data line
CLASS_LIKE = {"class", "interface", "enum", "record", "struct", "object", "mixin", "extension", "trait", "impl",
              "union", "module"}
KEYWORDS = {"if", "else", "for", "foreach", "while", "do", "switch", "case", "catch", "try", "finally", "return",
            "new", "throw", "await", "yield", "using", "lock", "fixed", "when", "is", "as", "in", "typeof",
            "sizeof", "nameof", "default", "goto", "break", "continue", "delete", "var", "let", "const",
            "super", "this", "function", "import", "package", "assert", "synchronized", "checked", "unchecked",
            "throws", "with", "match", "select", "go", "defer", "echo", "print", "elif", "unless", "until", "not",
            "and", "or", "operator", "get", "set", "init", "where", "on", "out", "ref", "params"}


class Found:
    """A symbol under construction (the outline module turns it into a Symbol)."""
    __slots__ = ("name", "kind", "start", "end", "parent", "signature")

    def __init__(self, name, kind, start, end, parent, signature):
        self.name, self.kind, self.start, self.end, self.parent, self.signature = \
            name, kind, start, end, parent, signature


# --------------------------------------------------------------------------- masking
REGEX_AFTER_CHAR = "(,=:[!&|?{};+-*%~^"  # not < or >: JSX `</b>` and `<p>/path`
REGEX_AFTER_WORD = re.compile(r"\b(?:return|typeof|case|in|of|delete|void|throw|new|yield|await|when)\s*$")
HEREDOC = re.compile(r"<<([~-]?)(['\"]?)([A-Za-z_]\w*)\2")


class _Masker:
    """code: strings and comments blanked; nc ("no comments"): only comments blanked. Newlines kept."""

    def __init__(self, text: str, lang: str):
        self.t, self.lang, self.n = text, lang, len(text)
        self.code, self.nc = list(text), list(text)

    def blank(self, a: int, b: int, comment: bool):
        for k in range(a, min(b, self.n)):
            if self.t[k] != "\n":
                self.code[k] = " "
                if comment:
                    self.nc[k] = " "

    def run(self) -> tuple[str, str]:
        t, n, lang = self.t, self.n, self.lang
        i, prev = 0, ""  # prev: last significant code char (regex-literal and JSX heuristics)
        heredocs: list[tuple[str, bool]] = []
        while i < n:
            c = t[i]
            nxt = t[i + 1] if i + 1 < n else ""
            if c == "\n":
                i += 1
                if heredocs:
                    i = self.heredoc_bodies(i, heredocs)
                    heredocs = []
                continue
            if lang != "ruby" and c == "/" and nxt == "/":
                j = t.find("\n", i)
                j = n if j < 0 else j
                self.blank(i, j, True)
                i = j
                continue
            if lang != "ruby" and c == "/" and nxt == "*":
                j = self.block_comment_end(i)
                self.blank(i, j, True)
                i = j
                continue
            if c == "#" and lang in ("php", "ruby") and not (lang == "php" and nxt == "["):
                j = t.find("\n", i)
                j = n if j < 0 else j
                self.blank(i, j, True)
                i = j
                continue
            if lang == "ruby":
                if (i == 0 or t[i - 1] == "\n") and t.startswith("=begin", i):
                    j = t.find("\n=end", i)
                    j = n if j < 0 else t.find("\n", j + 1) if t.find("\n", j + 1) >= 0 else n
                    self.blank(i, j, True)
                    i = j
                    continue
                m = HEREDOC.match(t, i)  # `<<~SQL`; `items << x` and `class << self` have a space after <<
                if m:
                    heredocs.append((m.group(3), bool(m.group(1))))
                    i = m.end()
                    prev = "x"
                    continue
                m = re.compile(r"%[wWiIqQr]?([(\[{<|!/])").match(t, i)
                if m and not (prev.isalnum() or prev in "_)]"):
                    j = self.percent_literal(m.end(), m.group(1))
                    self.blank(i, j, False)
                    i, prev = j, "x"
                    continue
            j = self.string_end(i, prev)
            if j:
                self.blank(i, j, False)
                i, prev = j, "x"
                continue
            if c == "/" and nxt != ">" and lang in ("js", "ts", "ruby") and (prev == "" or prev in REGEX_AFTER_CHAR or
                                                              REGEX_AFTER_WORD.search(t, max(0, i - 12), i)):
                j = self.regex_end(i)
                if j:
                    self.blank(i, j, False)
                    i, prev = j, "x"
                    continue
            if not c.isspace():
                prev = c
            i += 1
        return "".join(self.code), "".join(self.nc)

    def block_comment_end(self, i: int) -> int:
        """Rust, Kotlin and Dart nest /* /* */ */; the others end at the first */."""
        t, depth, j = self.t, 1, i + 2
        nests = self.lang in ("rust", "kotlin", "dart")
        while j < self.n:
            if t.startswith("*/", j):
                depth -= 1
                if depth == 0 or not nests:
                    return j + 2
                j += 2
            elif nests and t.startswith("/*", j):
                depth += 1
                j += 2
            else:
                j += 1
        return self.n

    def heredoc_bodies(self, i: int, heredocs) -> int:
        """Ruby heredoc bodies start on the line after `<<~ID` and end at the line holding ID."""
        for ident, _ in heredocs:
            while i < self.n:
                j = self.t.find("\n", i)
                j = self.n if j < 0 else j
                done = self.t[i:j].strip() == ident
                self.blank(i, j, False)
                i = j + 1
                if done:
                    break
        return min(i, self.n)

    def percent_literal(self, i: int, opener: str) -> int:
        closer = {"(": ")", "[": "]", "{": "}", "<": ">"}.get(opener, opener)
        depth = 1
        while i < self.n:
            ch = self.t[i]
            if ch == "\\":
                i += 2
                continue
            if ch == opener and closer != opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return self.n

    def regex_end(self, i: int) -> int:
        j, in_class = i + 1, False
        while j < self.n and self.t[j] != "\n":
            ch = self.t[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                j += 1
                while j < self.n and self.t[j].isalpha():
                    j += 1
                return j
            j += 1
        return 0

    def string_end(self, i: int, prev: str) -> int:
        """End (exclusive) of a string literal starting at i, or 0 when there is none."""
        t, lang = self.t, self.lang
        c = t[i]
        ident_before = i > 0 and (t[i - 1].isalnum() or t[i - 1] == "_")
        if lang == "rust":
            m = re.compile(r'b?r(#*)"').match(t, i)
            if m and not ident_before:
                close = '"' + m.group(1)
                j = t.find(close, m.end())
                return self.n if j < 0 else j + len(close)
            if c == "'":
                if t[i + 1:i + 2] == "\\":
                    j = t.find("'", i + 3)
                    return self.n if j < 0 else j + 1
                if t[i + 2:i + 3] == "'":
                    return i + 3
                return 0  # a lifetime: 'a
        if lang == "cs" and c in "$@":
            m = re.compile(r'(\$+@?|@\$*)"').match(t, i)
            if m:
                return self.cs_string(m.end(), "@" in m.group(1), "$" in m.group(1))
        if lang == "dart" and c == "r" and t[i + 1:i + 2] in ("'", '"') and not ident_before:
            q = t[i + 1:i + 4] if t[i + 1:i + 4] in ("'''", '"""') else t[i + 1]
            j = t.find(q, i + 1 + len(q))
            return self.n if j < 0 else j + len(q)
        if c not in "'\"`":
            return 0
        if c == "`" and lang not in ("js", "ts", "go"):
            return 0  # Kotlin backtick names are identifiers
        if lang in ("js", "ts") and c != "`" and ident_before:
            return 0  # JSX text: Don't
        triple = t[i:i + 3]
        if triple in ('"""', "'''") and lang in ("kotlin", "dart", "java", "cs") and (c == '"' or lang == "dart"):
            j = t.find(triple, i + 3)
            return self.n if j < 0 else j + 3
        if c == "`" and lang == "go":
            j = t.find("`", i + 1)
            return self.n if j < 0 else j + 1
        interp = {"js": "${", "ts": "${", "dart": "${", "kotlin": "${", "ruby": "#{", "php": None}.get(lang)
        if c == "`":
            interp = "${"
        elif c in "'\"" and lang in ("js", "ts"):
            interp = None
        elif c == "'" and lang in ("ruby", "php", "kotlin", "java", "cs", "go", "rust"):
            interp = None
        if c == "'" and lang in ("java", "cs", "go", "kotlin"):
            interp = None
        return self.plain_string(i + 1, c, interp)

    def plain_string(self, i: int, quote: str, interp: str | None) -> int:
        t = self.t
        while i < self.n:
            ch = t[i]
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                return i + 1
            if interp and t.startswith(interp, i):
                i = self.code_until_brace(i + len(interp))
                continue
            if ch == "\n" and quote != "`" and self.lang not in ("php", "ruby", "rust"):  # these allow newlines
                return i  # unterminated on this line: stop here rather than eat the file
            i += 1
        return self.n

    def cs_string(self, i: int, verbatim: bool, interpolated: bool) -> int:
        t = self.t
        while i < self.n:
            ch = t[i]
            if not verbatim and ch == "\\":
                i += 2
                continue
            if ch == '"':
                if verbatim and t[i + 1:i + 2] == '"':
                    i += 2
                    continue
                return i + 1
            if interpolated and ch == "{":
                if t[i + 1:i + 2] == "{":
                    i += 2
                    continue
                i = self.code_until_brace(i + 1)
                continue
            i += 1
        return self.n

    def code_until_brace(self, i: int) -> int:
        """Inside an interpolation: skip code, nested strings included, to the closing "}"."""
        depth, prev = 1, "{"
        while i < self.n:
            ch = self.t[i]
            j = self.string_end(i, "x") if ch in "'\"`" else 0
            if not j and ch == "/" and self.lang in ("js", "ts", "ruby") and prev in REGEX_AFTER_CHAR:
                j = self.regex_end(i)
            if j:
                i, prev = j, "x"
                continue
            if not ch.isspace():
                prev = ch
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return self.n


def mask(text: str, lang: str) -> tuple[str, str]:
    return _Masker(text, lang).run()


# --------------------------------------------------------------------------- start patterns
ID = r"[A-Za-z_$][\w$]*"
TS_MODS = r"(?:(?:export|default|declare|abstract|public|private|protected|static|readonly|override|async|accessor)\s+)*"
JAVA_MODS = (r"(?:(?:public|private|protected|static|final|abstract|synchronized|native|default|strictfp|sealed|"
             r"non-sealed|transient)\s+)*")
CS_MODS = (r"(?:(?:public|private|protected|internal|static|readonly|sealed|abstract|virtual|override|new|async|"
           r"unsafe|extern|partial|required|file|const)\s+)*")
KT_MODS = (r"(?:(?:public|private|protected|internal|open|final|abstract|sealed|data|enum|annotation|inner|value|"
           r"inline|override|suspend|operator|infix|tailrec|external|expect|actual|const|lateinit)\s+)*")
DART_MODS = r"(?:(?:abstract|base|final|interface|sealed|external|static|const|factory|late|covariant)\s+)*"
RUST_VIS = r"(?:pub(?:\s*\([^)]*\))?\s+)?"
GENERIC_TYPE = r"[\w.$]+(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?\??"  # Map<String, List<int>>? — no nested quantifier
TYPE_TOKENS = r"((?:[\w.$]+(?:<.*?>)?(?:\[\])*\??\s+)+?)"  # return type (and modifiers missed above)

# (regex, kind or None to take it from group 1, scope) — scope: any | class (directly in a class-like body)
PATTERNS = {
    "js": [
        (rf"^\s*{TS_MODS}function\s*\*?\s*({ID})", "function", "any"),
        (rf"^\s*{TS_MODS}class\s+({ID})", "class", "any"),
        (rf"^\s*(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+({ID})\s*(?::[^=]*)?=(?!=)", "arrow", "any"),
        (rf"^\s*{TS_MODS}(?:(?:get|set)\s+)?\*?\s*(#?{ID})\s*(?:<[^>]*>)?\s*\(", "method", "class"),
        (rf"^\s*{TS_MODS}(#?{ID})\s*(?::[^=]*)?=(?!=)", "field_arrow", "class"),
    ],
    "go": [
        (r"^func\s*(\([^)]*\)\s*)?([A-Za-z_]\w*)", "go_func", "any"),
        (r"^type\s+([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*(struct|interface)?", "go_type", "any"),
        (r"^\s+([A-Za-z_]\w*)\s*:?=\s*func\s*\(", "function", "any"),
    ],
    "java": [
        (rf"^\s*{JAVA_MODS}(class|interface|enum|record)\s+({ID})", None, "any"),
        (rf"^\s*{JAVA_MODS}(?:<.*?>\s+)?{TYPE_TOKENS}({ID})\s*\(", "typed", "class"),
        (rf"^\s*{JAVA_MODS}({ID})\s*\(", "ctor", "class"),
    ],
    "cs": [
        (rf"^\s*{CS_MODS}(class|interface|enum|struct|record(?:\s+(?:struct|class))?)\s+({ID})", None, "any"),
        (rf"^\s*{CS_MODS}{TYPE_TOKENS}({ID})\s*(?:<[^>]*>)?\s*\(", "typed", "any"),
        (rf"^\s*{CS_MODS}({ID})\s*\(", "ctor", "class"),
    ],
    "kotlin": [
        (rf"^\s*{KT_MODS}companion\s+object(?:\s+({ID}))?", "companion", "any"),
        (rf"^\s*{KT_MODS}(class|interface|object)\s+({ID})", None, "any"),
        (rf"^\s*{KT_MODS}typealias\s+({ID})", "type", "any"),
        (rf"^\s*{KT_MODS}fun\s+(?:<[^>]*>\s*)?(?:[\w.<>?, ]+?\.)?(`[^`]+`|{ID})\s*\(", "function", "any"),
    ],
    "dart": [
        (rf"^\s*{DART_MODS}(class|mixin|enum)\s+({ID})", None, "any"),
        (rf"^\s*extension\s+({ID})", "extension", "any"),
        (rf"^\s*typedef\s+({ID})", "type", "any"),
        (rf"^\s*(?:static\s+)?(?:{GENERIC_TYPE}\s+)?(?:get|set)\s+({ID})\b", "function", "any"),
        (rf"^\s*(?:(?:const|factory|external)\s+)*({ID}(?:\.{ID})?)\s*\(", "ctor", "class"),
        (rf"^\s*(?:(?:static|external|abstract)\s+)*{TYPE_TOKENS}({ID})\s*(?:<[^>]*>)?\s*\(", "typed", "any"),
    ],
    "rust": [
        (rf"^\s*{RUST_VIS}(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+)?fn\s+({ID})",
         "function", "any"),
        (rf"^\s*{RUST_VIS}(struct|enum|trait|union)\s+({ID})", None, "any"),
        (r"^\s*(?:unsafe\s+)?impl\b\s*(?:<.*?>)?\s*(?:[\w:]+(?:<.*?>)?\s+for\s+)?&?(?:\w+::)*([A-Za-z_]\w*)",
         "impl", "any"),
        (rf"^\s*{RUST_VIS}mod\s+({ID})", "module", "any"),
        (rf"^\s*{RUST_VIS}type\s+({ID})", "type", "any"),
    ],
    "php": [
        (rf"^\s*(?:(?:abstract|final|readonly)\s+)*(class|interface|trait|enum)\s+({ID})", None, "any"),
        (rf"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+&?\s*({ID})\s*\(",
         "function", "any"),
    ],
}
PATTERNS["ts"] = PATTERNS["js"][:2] + [
    (rf"^\s*{TS_MODS}(?:const\s+)?(interface|enum|type|namespace)\s+({ID})", None, "any"),
] + PATTERNS["js"][2:]
COMPILED = {lang: [(re.compile(rx), kind, scope) for rx, kind, scope in pats] for lang, pats in PATTERNS.items()}

ATTRIBUTE = {"java": r"@", "kotlin": r"@", "dart": r"@", "ts": r"@", "js": r"@", "rust": r"#\[", "php": r"#\[",
             "cs": r"\[.*\]$"}
CONTINUES_LINE = re.compile(r"^(\{|:|=>|->|=(?!=)|\.|,|\||&|\+|\?|extends\b|implements\b|with\b|where\b|throws\b|"
                            r"permits\b|on\b)")
ENDS_OPEN = re.compile(r"(=>|->|[=,(\[|&+\-*/?:.]|\b(?:extends|implements|with|where|throws))\s*$")
EXPR_CONTINUES = re.compile(r"^(\.|\?|:|\||&|\+|-(?!>)|\*|/|=>|->|\))")


# --------------------------------------------------------------------------- brace languages
class _Source:
    def __init__(self, text: str, lang: str):
        self.lang = lang
        self.code, self.nc = mask(text, lang)
        self.lines = self.code.split("\n")
        self.starts = [0]
        for line in self.lines[:-1]:
            self.starts.append(self.starts[-1] + len(line) + 1)
        self.match: dict[int, int] = {}
        stack = []
        for k, ch in enumerate(self.code):
            if ch == "{":
                stack.append(k)
            elif ch == "}" and stack:
                self.match[stack.pop()] = k

    def line_of(self, pos: int) -> int:
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo  # 0-based

    def next_code_line(self, line: int) -> str:
        for k in range(line + 1, min(line + 6, len(self.lines))):
            if self.lines[k].strip():
                return self.lines[k].strip()
        return ""

    def scan(self, line: int) -> tuple[int, int]:
        """From the start of a declaration line: (0-based end line, end of the header in the text)."""
        code, n = self.code, len(self.code)
        i = self.starts[line]
        depth = angle = 0
        where = False
        lines_seen = 0
        while i < n:
            ch = code[i]
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(depth - 1, 0)
            elif depth == 0:
                two = code[i:i + 2]
                if two in ("=>", "->"):
                    if angle == 0 and two == "=>":
                        return self.expression(i + 2), i
                    i += 2
                    continue
                if ch == "<" and angle >= 0 and self.lang != "go":
                    angle += 1
                elif ch == ">" and angle > 0:
                    angle -= 1
                elif angle == 0:
                    if ch == "{":
                        close = self.match.get(i)
                        return (self.line_of(close) if close is not None else len(self.lines) - 1), i
                    if ch == ";":
                        return self.line_of(i), i
                    if ch == "=" and code[i + 1:i + 2] != "=" and code[i - 1:i] not in ("=", "!", "<", ">", ":"):
                        arrow = _js_callable(code, i + 1) if self.lang in ("js", "ts") else 0
                        return self.expression(i + 1), arrow or i
                    if code.startswith("where", i) and not code[i - 1:i].isalnum() and \
                            not code[i + 5:i + 6].isalnum():
                        where = True
                    if ch == "\n":
                        here = self.line_of(i)
                        lines_seen += 1
                        if not where and not ENDS_OPEN.search(self.lines[here]) and \
                                not CONTINUES_LINE.match(self.next_code_line(here)):
                            return here, i
                        if lines_seen > 40:
                            return line, i
            i += 1
        return len(self.lines) - 1, n

    def expression(self, i: int) -> int:
        """End line of an expression body: ";" or a line end at depth 0 that does not continue."""
        code, n = self.code, len(self.code)
        while i < n and code[i].isspace():
            i += 1
        depth = 0
        while i < n:
            ch = code[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth < 0:
                    return self.line_of(i)
            elif depth == 0 and ch in ";,":
                return self.line_of(i)
            elif depth == 0 and ch == "\n":
                here = self.line_of(i)
                if not ENDS_OPEN.search(self.lines[here]) and not EXPR_CONTINUES.match(self.next_code_line(here)):
                    return here
            i += 1
        return len(self.lines) - 1

    def attributes_start(self, line: int) -> int:
        """Annotations / attributes / decorators right above a declaration belong to it."""
        rx = ATTRIBUTE.get(self.lang)
        if not rx:
            return line
        attr = re.compile(r"^" + rx)
        start, j = line, line - 1
        while j >= 0:
            s = self.lines[j].strip()
            if not s:
                break
            if attr.match(s):
                start, j = j, j - 1
                continue
            bal, k = 0, j  # last line of a multi-line attribute: find where its brackets open
            while k >= 0:
                bal += sum(self.lines[k].count(o) - self.lines[k].count(c) for o, c in ("()", "[]", "{}"))
                if bal >= 0:
                    break
                k -= 1
            if k >= 0 and k < j and bal == 0 and attr.match(self.lines[k].strip()):
                start, j = k, k - 1
                continue
            break
        return start

    def signature(self, line: int, header_end: int) -> str:
        a = self.starts[line]
        text = self.nc[a:max(header_end, a)]
        sig = re.sub(r"\s+", " ", text).strip()
        sig = re.sub(r"\s*[{;]$|\s*=$", "", sig).strip()
        return sig if len(sig) <= 200 else sig[:199] + "…"


def _js_callable(code: str, i: int) -> int:
    """After `name =`: the end of `async? (params) =>` / `ident =>` / `function`, or 0 when it is not one."""
    m = re.compile(r"\s*(?:async\s*)?").match(code, i)
    i = m.end()
    if code.startswith("function", i):
        return i
    if i < len(code) and code[i] == "(":
        depth, j = 0, i
        while j < len(code):
            if code[j] == "(":
                depth += 1
            elif code[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        m = re.compile(r"\)\s*(?::[^=;{]*?)?\s*=>").match(code, j)
        return m.end() if m else 0
    m = re.compile(r"[A-Za-z_$][\w$]*\s*=>").match(code, i)
    return m.end() if m else 0


def extract_brace(text: str, lang: str) -> list[Found]:
    src = _Source(text, lang)
    out: list[Found] = []
    stack: list[tuple[Found, str]] = []  # (symbol, dotted name)
    for li, line in enumerate(src.lines):
        if not line.strip():
            continue
        while stack and stack[-1][0].end < li + 1:
            stack.pop()
        scope = stack[-1][0] if stack else None
        in_class = scope is not None and scope.kind in CLASS_LIKE
        hit = _match_line(src, lang, li, line[:MAX_DECL_CHARS], scope, in_class)
        if not hit:
            continue
        name, kind = hit
        end, header_end = src.scan(li)
        if kind in ("function", "method"):
            kind = "method" if in_class else "function"
        sym = Found(name, kind, src.attributes_start(li) + 1, max(end, li) + 1,
                    stack[-1][1] if stack else None, src.signature(li, header_end))
        out.append(sym)
        stack.append((sym, f"{stack[-1][1]}.{name}" if stack else name))
    return out


def _match_line(src: _Source, lang: str, li: int, line: str, scope, in_class: bool):
    for rx, kind, where in COMPILED[lang]:
        if where == "class" and not in_class:
            continue
        m = rx.match(line)
        if not m:
            continue
        if kind is None:  # keyword in group 1, name in group 2
            word = m.group(1).split()[0]
            return m.group(2), "record" if word == "record" else word
        if kind == "go_func":
            return m.group(2), "method" if m.group(1) else "function"
        if kind == "go_type":
            return m.group(1), m.group(2) or "type"
        if kind == "companion":
            return m.group(1) or "Companion", "object"
        if kind in ("arrow", "field_arrow"):
            if not _js_callable(src.code, src.starts[li] + m.end()):
                continue
            return m.group(1), "function"
        if kind == "typed":
            types = m.group(1).split()
            name = m.group(2)
            if name in KEYWORDS or any(t.split("<")[0] in KEYWORDS for t in types):
                continue
            return name, "function"
        if kind == "ctor":
            name = m.group(1)
            if scope is None or name.split(".")[0] != scope.name:
                continue
            return name, "method"
        if kind == "method":
            name = m.group(1)
            if name in KEYWORDS:
                continue
            return name, "method"
        name = m.group(1).strip("`")
        return name, kind
    return None


# --------------------------------------------------------------------------- Ruby
RUBY_WORD = re.compile(r"(?<![\w.:@$])(def|class|module|if|unless|while|until|case|begin|for|do|end)(?![\w?!:])")
RUBY_DEF = re.compile(r"def\s+(?:self\.)?([\w?!=\[\]<>+\-*/%~]+|`[^`]+`)")
RUBY_ENDLESS = re.compile(r"def\s+(?:self\.)?[\w?!\[\]<>+\-*/%~]+[?!]?\s*(?:\([^)]*\))?\s*=(?![=~>])")
RUBY_NAME = re.compile(r"(?:class|module)\s+([\w:]+)")


def extract_ruby(text: str) -> list[Found]:
    code, nc = mask(text, "ruby")
    lines, nc_lines = code.split("\n"), nc.split("\n")
    out: list[Found] = []
    stack: list[tuple[Found | None, str | None]] = []  # open blocks: (symbol or None, dotted name)

    def owner():
        for sym, dotted in reversed(stack):
            if sym is not None:
                return sym, dotted
        return None, None

    for li, line in enumerate(lines):
        offset = 0
        for stmt in line.split(";"):
            loop_head = re.match(r"\s*(while|until|for)\b", stmt) is not None
            for m in RUBY_WORD.finditer(stmt):
                w, before = m.group(1), stmt[:m.start()]
                at_start = not before.strip() or re.search(r"(=|\(|\|\||&&|\breturn|\bthen|\band|\bor|\bnot)\s*$",
                                                           before)
                if w == "end":
                    if stack:
                        sym, _ = stack.pop()
                        if sym is not None:
                            sym.end = li + 1
                    continue
                if w == "def":
                    rest = stmt[m.start():]
                    dm = RUBY_DEF.match(rest)
                    if not dm:
                        continue
                    parent_sym, parent_name = owner()
                    kind = "method" if parent_sym is not None and parent_sym.kind in CLASS_LIKE else "function"
                    sig = re.sub(r"\s+", " ", nc_lines[li][offset + m.start():offset + len(stmt)]).strip()
                    sym = Found(dm.group(1).strip("`"), kind, li + 1, li + 1, parent_name, sig)
                    out.append(sym)
                    if not RUBY_ENDLESS.match(rest):
                        stack.append((sym, f"{parent_name}.{sym.name}" if parent_name else sym.name))
                    continue
                if w in ("class", "module"):
                    rest = stmt[m.end():]
                    if w == "class" and rest.lstrip().startswith("<<"):
                        stack.append((None, None))
                        continue
                    nm = RUBY_NAME.match(stmt[m.start():])
                    if not nm:
                        continue
                    _, parent_name = owner()
                    name = nm.group(1).split("::")[-1]
                    sig = re.sub(r"\s+", " ", nc_lines[li][offset + m.start():offset + len(stmt)]).strip()
                    sym = Found(name, w, li + 1, li + 1, parent_name, sig)
                    out.append(sym)
                    stack.append((sym, f"{parent_name}.{name}" if parent_name else name))
                    continue
                if w == "do":
                    if not loop_head:
                        stack.append((None, None))
                    continue
                if at_start:  # if/unless/while/until/case/begin/for as statements, not modifiers
                    stack.append((None, None))
            offset += len(stmt) + 1
    return out
