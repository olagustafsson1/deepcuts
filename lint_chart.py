#!/usr/bin/env python3
"""Nashville chart linter — validates chord/bar notation in *_numbers.md files."""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Token patterns
# ---------------------------------------------------------------------------

DEGREE = r'[b#]?[1-7]'
QUALITY = (
    r'(?:'
    r'(?:m7?b?5?)'          # minor family: m, m7, mb5, m7b5
    r'|(?:D(?:sus|[b#]?(?:9|11|13))?)'  # dominant family: D, D9, D11, D13, Dsus, Db9, D#9
    r'|(?:(?:M|maj|\^)(?:9)?)'           # major 7 family: M, maj, ^, M9, maj9, ^9
    r'|(?:sus(?:/' + DEGREE + r')?)'     # suspended: sus, sus/5
    r'|dim'                  # diminished
    r'|ø'                    # half-diminished
    r'|(?:add(?:9|11|13))'   # added tones
    r'|(?:[b#]?(?:9|11|13))' # extensions
    r')*'
)
SLASH_BASS = r'(?:/' + DEGREE + r')?'
RHYTHM = r'[.\-]*'

NASHVILLE_RE = re.compile(
    r'^(' + DEGREE + QUALITY + SLASH_BASS + r')(' + RHYTHM + r')$'
)
REPEAT_RE = re.compile(r'^(%)(' + RHYTHM + r')$')
REST_RE = re.compile(r'^(-+)(' + RHYTHM + r')$')

LETTER_ROOT_RE = re.compile(
    r'^[A-G][b#]?'
    r'(?:m(?:aj|in)?7?b?5?|M|maj|dim|aug|sus|ø|[0-9]|add|\^|/|\.|-|\+|#)'
)

STRIP_ANNOTATION_RE = re.compile(r'\([^)]*\)')
STRIP_REPEAT_COUNT_RE = re.compile(r'\b[Xx]\d+\b')

FREE_TEXT_KEYWORDS = {'riff', 'fill', 'drum', 'cont', 'cont.', 'mel', 'melody',
                      'break', 'solo', 'intro', 'outro', 'stop', 'tacet'}

LOWERCASE_DOM_RE = re.compile(r'^([b#]?[1-7])d(\d.*)?$')

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Diagnostic:
    file: str
    line: int
    bar: int | None
    code: str
    severity: str  # "error" or "warning"
    message: str

    def __str__(self):
        loc = f"{self.file}:{self.line}"
        bar_str = f" bar {self.bar}:" if self.bar else ""
        return f"{loc}:{bar_str} {self.code} ({self.severity}): {self.message}"


@dataclass
class Bar:
    line_num: int
    bar_num: int
    raw: str
    tokens: list[str] = field(default_factory=list)
    is_free_text: bool = False

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def strip_line(line: str) -> str:
    """Remove annotations and repeat counts, preserving bar structure."""
    line = STRIP_ANNOTATION_RE.sub('', line)
    line = STRIP_REPEAT_COUNT_RE.sub('', line)
    return line.rstrip()


def is_bar_line(line: str) -> bool:
    return '[' in line


def extract_bars(line: str, line_num: int, bar_counter: int) -> list[Bar]:
    """Split a line into Bar objects at each `[`."""
    cleaned = strip_line(line)
    parts = cleaned.split('[')
    bars = []
    for part in parts[1:]:  # skip text before first [
        raw = part.strip()
        bar = Bar(line_num=line_num, bar_num=bar_counter, raw=raw)
        bar_counter += 1

        if not raw:
            bars.append(bar)
            continue

        first_word = raw.split()[0].rstrip('.-') if raw.split() else ''
        if first_word.lower() in FREE_TEXT_KEYWORDS:
            bar.is_free_text = True
            bars.append(bar)
            continue

        bar.tokens = raw.split()
        bars.append(bar)

    return bars


def token_beats(token: str) -> int:
    """Count beats for a token: 1 (symbol) + trailing dots/dashes."""
    m = NASHVILLE_RE.match(token) or REPEAT_RE.match(token) or REST_RE.match(token)
    if m:
        return 1 + len(m.group(2))
    # Fallback: count trailing rhythm chars
    i = len(token)
    while i > 0 and token[i - 1] in '.-':
        i -= 1
    return 1 + (len(token) - i)

# ---------------------------------------------------------------------------
# Lint rules
# ---------------------------------------------------------------------------

def lint_file(path: Path, time_sig: int = 4) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    lines = path.read_text(encoding='utf-8').splitlines()
    fname = str(path)

    bar_counter = 1
    quality_usage: dict[str, set[str]] = {}  # degree -> set of maj7 synonyms used

    for line_num_0, raw_line in enumerate(lines):
        line_num = line_num_0 + 1
        if not is_bar_line(raw_line):
            continue

        bars = extract_bars(raw_line, line_num, bar_counter)
        bar_counter += len(bars)

        has_bare_degrees = False
        has_annotation = bool(STRIP_ANNOTATION_RE.search(raw_line))

        for bar in bars:
            if bar.is_free_text:
                continue
            if not bar.tokens:
                continue

            beat_total = 0

            for tok in bar.tokens:
                # E004: dot-only bar
                if re.fullmatch(r'[.\-]+', tok) and not tok.startswith('-'):
                    diagnostics.append(Diagnostic(
                        fname, line_num, bar.bar_num, 'E004', 'error',
                        f'dot-only token `{tok}` — use `%` for carry-over'
                    ))
                    beat_total += len(tok)
                    continue

                # E003: lowercase dominant
                lc = LOWERCASE_DOM_RE.match(tok.split('.')[0].split('-')[0])
                if lc:
                    diagnostics.append(Diagnostic(
                        fname, line_num, bar.bar_num, 'E003', 'error',
                        f'lowercase dominant `{tok}` — use uppercase D'
                    ))
                    beat_total += token_beats(tok)
                    continue

                # E002: letter-root chord
                base = tok.rstrip('.-')
                if base and LETTER_ROOT_RE.match(base):
                    diagnostics.append(Diagnostic(
                        fname, line_num, bar.bar_num, 'E002', 'error',
                        f'letter-root chord `{tok}` — convert to Nashville degree'
                    ))
                    beat_total += token_beats(tok)
                    continue

                # Try Nashville parse
                if NASHVILLE_RE.match(tok):
                    beat_total += token_beats(tok)

                    # Track quality for W002
                    m = re.match(r'([b#]?[1-7])(.*?)(/[b#]?[1-7])?([.\-]*)$', tok)
                    if m:
                        deg, qual = m.group(1), m.group(2)
                        maj7_syns = {'M', 'maj', '^', 'M9', 'maj9', '^9'}
                        if qual in maj7_syns:
                            quality_usage.setdefault(deg, set()).add(qual)

                        # Bare degree detection
                        if not qual and not m.group(3):
                            has_bare_degrees = True

                    continue

                if REPEAT_RE.match(tok):
                    beat_total += token_beats(tok)
                    continue

                if REST_RE.match(tok):
                    beat_total += token_beats(tok)
                    continue

                # E001: unrecognized
                diagnostics.append(Diagnostic(
                    fname, line_num, bar.bar_num, 'E001', 'error',
                    f'unrecognized token `{tok}`'
                ))
                beat_total += token_beats(tok)

            # E005: beat count
            if beat_total != time_sig and beat_total > 0:
                diagnostics.append(Diagnostic(
                    fname, line_num, bar.bar_num, 'E005', 'error',
                    f'bar has {beat_total} beats (expected {time_sig})'
                ))

        # W001: bare degrees without comment
        if has_bare_degrees and not has_annotation:
            diagnostics.append(Diagnostic(
                fname, line_num, None, 'W001', 'warning',
                'bare degrees without end-of-line comment (melody/bass?)'
            ))

    # W002: quality synonym mix per degree
    for deg, syns in quality_usage.items():
        canonical = {s.rstrip('9') for s in syns}
        if len(canonical) > 1:
            diagnostics.append(Diagnostic(
                fname, 0, None, 'W002', 'warning',
                f'degree `{deg}` uses mixed maj7 spellings: {", ".join(sorted(syns))}'
            ))

    return diagnostics

# ---------------------------------------------------------------------------
# Bar numbering output
# ---------------------------------------------------------------------------

def number_bars(path: Path) -> str:
    lines = path.read_text(encoding='utf-8').splitlines()

    # First pass: count total bars to determine column width
    total_bars = 0
    for line in lines:
        if is_bar_line(line):
            cleaned = strip_line(line)
            total_bars += cleaned.count('[')

    width = max(len(str(total_bars)), 2)
    bar_counter = 1
    output_lines = []

    for line in lines:
        if is_bar_line(line):
            prefix = str(bar_counter).rjust(width)
            output_lines.append(f"{prefix} {line}")
            cleaned = strip_line(line)
            bar_counter += cleaned.count('[')
        else:
            output_lines.append(f"{' ' * width} {line}")

    return '\n'.join(output_lines)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Nashville chart linter')
    parser.add_argument('files', nargs='*', help='Chart files to lint (default: *_numbers.md)')
    parser.add_argument('--number-bars', action='store_true', help='Print files with bar numbers')
    parser.add_argument('--time-sig', type=int, default=4, help='Beats per bar (default: 4)')
    parser.add_argument('--warnings', '-w', action='store_true', help='Include warnings')
    args = parser.parse_args()

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = sorted(Path('.').glob('*_numbers.md'))

    if args.number_bars:
        for path in paths:
            if not path.exists():
                print(f"File not found: {path}", file=sys.stderr)
                continue
            print(f"--- {path} ---")
            print(number_bars(path))
            print()
        return

    error_count = 0
    warn_count = 0
    for path in paths:
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            continue
        diags = lint_file(path, time_sig=args.time_sig)
        for d in diags:
            if d.severity == 'warning' and not args.warnings:
                continue
            print(d)
            if d.severity == 'error':
                error_count += 1
            else:
                warn_count += 1

    if error_count or warn_count:
        print(f"\n{error_count} error(s), {warn_count} warning(s)")
    else:
        print("No issues found.")

    sys.exit(1 if error_count else 0)


if __name__ == '__main__':
    main()
