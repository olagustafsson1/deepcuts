#!/usr/bin/env python3
"""Nashville chart linter — chord charts, filenames, and structured headers.

Source charts live in raw/ (hand-edited only; tooling must not write there).
With ``--output-dir``, the linter applies **autofix**, lints the result, and writes hyphenated
filenames under that directory — **raw/ is never written**.

With ``--in-place``, autofix is written back to each **given** path (same filename); paths under
``raw/`` are refused. Mutually exclusive with ``--output-dir``.

Autofixed bodies include a **blank line** between the structured header and the first chord line.

Charts should start (inside ```) with the header block used in raw/sledgehammer_115.md:
Song title:, Performing artist:, Original artist:, Original key:, Original tempo:,
New key:, New tempo:
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RAW_DIR = Path('raw')

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


# Chart metadata (filename + header)
# Filename: <title-slug>_<bpm>.md | <slug>_<bpm>_numbers.md | <slug>_<bpm>_<year>.md
# Structured header (top of file, inside ```), see sledgehammer_115.md
STRUCTURED_HEADER_LABELS: list[tuple[str, str]] = [
    (r'Song title\s*:', 'Song title:'),
    (r'Performing artist\s*:', 'Performing artist:'),
    (r'Original artist\s*:', 'Original artist:'),
    (r'Original key\s*:', 'Original key:'),
    (r'Original tempo\s*:', 'Original tempo:'),
    (r'New key\s*:', 'New key:'),
    (r'New tempo\s*:', 'New tempo:'),
]
RE_HEADER_BPM = re.compile(r'(?i)(?<![\d/])(\d{2,3})\s*bpm\b')
RE_VALID_STEM = re.compile(r'^[a-z0-9][a-z0-9_-]*$')

STRUCT_HEADER_KEYS = [
    'song_title', 'performing_artist', 'original_artist', 'original_key',
    'original_tempo', 'new_key', 'new_tempo',
]


def parse_chart_filename(name: str) -> tuple[str, int] | None:
    """
    Return (title_slug, bpm) if name matches chart file convention, else None.
    Accepts: <slug>_<bpm>.md, <slug>_<bpm>_numbers.md, <slug>_<bpm>_<year>.md
    """
    if not name.endswith('.md'):
        return None
    base = name[:-3]
    if base.endswith('_numbers'):
        m = re.match(r'^(.*)_(\d{2,3})_numbers$', base)
        if m:
            return m.group(1), int(m.group(2))
        return None
    m = re.match(r'^(.*)_(\d{2,3})_(\d{4})$', base)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r'^(.*)_(\d{2,3})$', base)
    if m:
        bpm = int(m.group(2))
        # avoid matching a trailing date fragment (e.g. ..._20250125 -> _25)
        if 20 <= bpm <= 250:
            return m.group(1), bpm
    return None


def slugify_title(title: str) -> str:
    t = title.strip().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t


def chart_header_block(lines: list[str]) -> str:
    """Lines before the first chord bar `[...`. Skips ``` fence markers."""
    buf: list[str] = []
    for line in lines[:100]:
        if line.strip() == '```':
            continue
        if re.match(r'^\s*\[', line):
            break
        buf.append(line)
    return '\n'.join(buf)


def first_line_title(header: str) -> str | None:
    for line in header.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith('#'):
            s = s.lstrip('#').strip()
        if ',' in s:
            return s.split(',')[0].strip()
        return None
    return None


def content_start_index(lines: list[str]) -> int:
    """Skip leading ``` fence line if present."""
    if lines and lines[0].strip() == '```':
        return 1
    return 0


def validate_structured_header(lines: list[str], fname: str, start: int) -> list[Diagnostic]:
    """Require sledgehammer-style label block at top (after optional opening ```)."""
    out: list[Diagnostic] = []
    j = start
    for pattern, label in STRUCTURED_HEADER_LABELS:
        while j < len(lines) and lines[j].strip() == '':
            j += 1
        ln = j + 1
        if j >= len(lines):
            out.append(Diagnostic(
                fname, ln, None, 'M008', 'error',
                f'missing required header line `{label}` at top (template: sledgehammer_115.md)',
            ))
            return out
        if not re.match(rf'(?i)^{pattern}\s*', lines[j]):
            out.append(Diagnostic(
                fname, ln, None, 'M008', 'error',
                f'expected line starting with `{label}`, got: {lines[j]!r}',
            ))
            return out
        j += 1
    return out


def extract_structured_values(lines: list[str], start: int) -> dict[str, str]:
    j = start
    vals: dict[str, str] = {}
    for (pattern, _), key in zip(STRUCTURED_HEADER_LABELS, STRUCT_HEADER_KEYS):
        while j < len(lines) and lines[j].strip() == '':
            j += 1
        if j >= len(lines):
            vals[key] = ''
            break
        m = re.match(rf'(?i)^{pattern}\s*(.*)$', lines[j])
        vals[key] = (m.group(1).strip() if m else '')
        j += 1
    return vals


def bpm_from_original_tempo(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r'(\d{2,3})', text)
    return int(m.group(1)) if m else None


def lint_chart_metadata(path: Path, lines: list[str] | None = None) -> list[Diagnostic]:
    """Filename slug/bpm + structured header (Song title … New tempo) like sledgehammer_115.md."""
    out: list[Diagnostic] = []
    fname = str(path)
    name = path.name

    parsed = parse_chart_filename(name)
    if parsed is None:
        out.append(Diagnostic(
            fname, 1, None, 'M001', 'error',
            'filename must be <title-slug>_<bpm>.md, <slug>_<bpm>_numbers.md, '
            'or <slug>_<bpm>_<year>.md (slug: lowercase, words separated by _)',
        ))
        return out

    stem, file_bpm = parsed
    if not RE_VALID_STEM.match(stem):
        out.append(Diagnostic(
            fname, 1, None, 'M002', 'error',
            f"filename slug `{stem}` must be lowercase words separated by _ (a-z0-9 only)",
        ))

    if lines is None:
        lines = path.read_text(encoding='utf-8').splitlines()
    cs = content_start_index(lines)
    struct_errs = validate_structured_header(lines, fname, cs)
    out.extend(struct_errs)
    if struct_errs:
        return out

    vals = extract_structured_values(lines, cs)
    title = vals.get('song_title', '')
    if title and RE_VALID_STEM.match(stem):
        ts = slugify_title(title)
        if ts and ts != stem and not stem.startswith(ts + '_'):
            out.append(Diagnostic(
                fname, 1, None, 'M007', 'warning',
                f"Song title slug `{ts}` does not match filename slug `{stem}` "
                '(filename = song title only; optional _suffix before _bpm)',
            ))

    hbpm = bpm_from_original_tempo(vals.get('original_tempo', ''))
    if hbpm is None:
        header = chart_header_block(lines)
        hbpm_m = RE_HEADER_BPM.search(header)
        if hbpm_m:
            hbpm = int(hbpm_m.group(1))
    if hbpm is None:
        out.append(Diagnostic(
            fname, 1, None, 'M004', 'error',
            'Original tempo: must include bpm (e.g. "115 bpm" or "115") or add "NN bpm" in header',
        ))
    elif hbpm != file_bpm:
        out.append(Diagnostic(
            fname, 1, None, 'M005', 'warning',
            f'tempo from header ({hbpm}) does not match filename bpm ({file_bpm})',
        ))

    if not vals.get('original_key', '').strip():
        out.append(Diagnostic(
            fname, 1, None, 'M009', 'warning',
            'Original key: is empty — set the key or "(same as chart)" if spelled out below',
        ))
    nk = vals.get('new_key', '').strip()
    if not nk:
        out.append(Diagnostic(
            fname, 1, None, 'M006', 'warning',
            'New key: is empty — use "New key: (no change)" if same as original',
        ))

    return out

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

def lint_file(
    path: Path,
    time_sig: int = 4,
    metadata: bool = True,
    content: str | None = None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if content is not None:
        lines = content.splitlines()
    else:
        lines = path.read_text(encoding='utf-8').splitlines()
    if metadata:
        diagnostics.extend(lint_chart_metadata(path, lines))
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
# Autofix (mechanical only; written to --output-dir — never modify raw/)
# ---------------------------------------------------------------------------

RE_LEADING_BAR_NUM = re.compile(r'^(\s*)\d{1,4}\s+(?=\[)')


def build_structured_header_from_legacy(lines: list[str], cs: int, first_bar: int, path: Path) -> list[str] | None:
    chunk = lines[cs:first_bar]
    block = '\n'.join(chunk)
    if re.search(r'(?mi)^Song title\s*:', block):
        return None
    tlines = [L for L in chunk if L.strip() and L.strip() != '```']
    if not tlines:
        return None
    first = tlines[0].strip()
    title_p = performing = orig_art = ''
    hbpm: int | None = None
    key_hint = ''

    m = re.match(
        r'^([^,]+),\s*([^,]+),\s*(\d{2,3})\s*bpm(?:\s*,\s*(.+))?\s*$',
        first, re.I,
    )
    if m:
        title_p = m.group(1).strip()
        performing = m.group(2).strip()
        orig_art = performing
        hbpm = int(m.group(3))
        if m.group(4):
            key_hint = m.group(4).strip()
    else:
        m2 = re.match(r'^([^,]+),\s*([^,]+),\s*bpm\s*(\d{2,3})\s*$', first, re.I)
        if m2:
            title_p = m2.group(1).strip()
            performing = m2.group(2).strip()
            orig_art = performing
            hbpm = int(m2.group(3))
        else:
            m3 = re.match(r'^([^,]+),\s*(.+)$', first)
            if m3:
                title_p = m3.group(1).strip()
                performing = m3.group(2).strip()
                orig_art = performing
    if not title_p:
        return None

    key_from_line = ''
    mk = re.search(r'(?mi)^Key:\s*(.+)$', block)
    if mk:
        body = mk.group(1).strip()
        parts = [p.strip() for p in body.split(',')]
        if parts:
            key_from_line = parts[0].strip()
        for p in parts[1:]:
            bm = re.search(r'(\d{2,3})\s*bpm', p, re.I)
            if bm:
                hbpm = int(bm.group(1))
        if hbpm is None:
            bm = re.search(r'(\d{2,3})\s*bpm', body, re.I)
            if bm:
                hbpm = int(bm.group(1))
    if key_hint and not key_from_line:
        key_from_line = key_hint

    parsed = parse_chart_filename(path.name)
    file_bpm = parsed[1] if parsed else None
    tempo_line = hbpm if hbpm is not None else file_bpm
    if tempo_line is None:
        bm = re.search(r'(\d{2,3})\s*bpm', block, re.I)
        if bm:
            tempo_line = int(bm.group(1))
    if tempo_line is None:
        tm = re.search(r'(\d{2,3})', first)
        if tm:
            cand = int(tm.group(1))
            if 20 <= cand <= 250:
                tempo_line = cand
    if tempo_line is None:
        return None

    ok = key_from_line or '(see chart)'
    return [
        f'Song title: {title_p}',
        f'Performing artist: {performing}',
        f'Original artist: {orig_art}',
        f'Original key: {ok}',
        f'Original tempo: {tempo_line} bpm',
        'New key: (no change)',
        'New tempo: (no change)',
    ]


def autofix_header_block(lines: list[str], path: Path) -> list[str]:
    cs = content_start_index(lines)
    first_bar = next((i for i in range(cs, len(lines)) if '[' in lines[i]), None)
    if first_bar is None:
        return lines
    if not validate_structured_header(lines, str(path), cs):
        return lines
    new_head = build_structured_header_from_legacy(lines, cs, first_bar, path)
    if not new_head:
        return lines
    return lines[:cs] + new_head + lines[first_bar:]


def fix_token_e003(tok: str) -> str:
    base = tok.split('.')[0].split('-')[0]
    if LOWERCASE_DOM_RE.match(base):
        return re.sub(r'^([b#]?[1-7])d', r'\1D', tok, count=1)
    return tok


def fix_token_dot(tok: str) -> str:
    if tok == '.':
        return '%'
    return tok


def fix_one_token(tok: str) -> str:
    if tok.startswith('['):
        return '[' + fix_token_e003(fix_token_dot(tok[1:]))
    return fix_token_e003(fix_token_dot(tok))


def fix_bar_line(line: str) -> str:
    if '[' not in line:
        return line
    line = RE_LEADING_BAR_NUM.sub(r'\1', line)
    line = re.sub(
        r'\s+(soft|fade|build|break|smooth)\s*$',
        r' (\1)',
        line,
        flags=re.I,
    )
    line = re.sub(r'(?<![\w/])b3\.1m\.', 'b3. 1m.', line)
    line = re.sub(r'(?<![\w/])b6\.1m\.', 'b6. 1m.', line)
    line = re.sub(r'1/5\.4/5\.', '1/5. 4/5.', line)
    line = re.sub(r'5\.4/5\.', '5. 4/5.', line)
    line = re.sub(
        r'([b#]?[1-7])\.([b#]?[1-7]/[b#]?[1-7])\.(?=\s|$|\[)',
        r'\1. \2.',
        line,
    )
    line = re.sub(r'([b#]?[1-7]/[b#]?[1-7])\.([b#]?[1-7]/[b#]?[1-7])\.', r'\1. \2.', line)
    line = re.sub(
        r'(?<![\w/])([b#]?[1-7])\.([b#]?[1-7])\.(?=\s|$|\[)',
        r'\1. \2.',
        line,
    )
    # Slash bass split from chord: "2m /3" -> "2m/3"
    line = re.sub(r'(\S+)\s+(/[b#]?[1-7])(?=\s|$|\[)', r'\1\2', line)
    parts = line.split()
    parts = [fix_one_token(p) for p in parts]
    return ' '.join(parts)


def ensure_blank_before_first_bars(lines: list[str]) -> list[str]:
    """Insert one empty line before the first chord line so header and bars are separated by \\n\\n."""
    cs = content_start_index(lines)
    for i in range(cs, len(lines)):
        if '[' not in lines[i] or lines[i].strip() == '```':
            continue
        if i > cs and lines[i - 1].strip() == '':
            return lines
        lines.insert(i, '')
        return lines
    return lines


def linted_output_basename(path: Path) -> str:
    """Hyphenated slug filename for linted/ (raw/ keeps underscores)."""
    return path.stem.replace('_', '-') + '.md'


def autofix_chart(text: str, path: Path) -> str:
    lines = text.splitlines()
    lines = autofix_header_block(lines, path)
    out: list[str] = []
    for line in lines:
        if is_bar_line(line):
            out.append(fix_bar_line(line))
        else:
            out.append(line)
    lines = ensure_blank_before_first_bars(out)
    body = '\n'.join(lines)
    if text.endswith('\n'):
        body += '\n'
    return body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def default_chart_paths() -> list[Path]:
    """Charts in raw/: valid <slug>_<bpm> filename; excludes README/LEGEND."""
    skip = {'README.md', 'LEGEND.md'}
    out: list[Path] = []
    if not RAW_DIR.is_dir():
        return out
    for p in sorted(RAW_DIR.glob('*.md')):
        if p.name in skip:
            continue
        if parse_chart_filename(p.name):
            out.append(p)
    return out


def assert_output_dir_not_under_raw(out_dir: Path) -> None:
    """Refuse writes under raw/ so source files stay tool-immutable."""
    resolved = out_dir.resolve()
    raw_resolved = RAW_DIR.resolve()
    try:
        resolved.relative_to(raw_resolved)
    except ValueError:
        return
    print(f'refuse: --output-dir must not be under raw/ (got {out_dir})', file=sys.stderr)
    sys.exit(1)


def path_is_under_raw(path: Path) -> bool:
    try:
        path.resolve().relative_to(RAW_DIR.resolve())
    except ValueError:
        return False
    return True


def assert_in_place_targets_not_raw(paths: list[Path]) -> None:
    bad = [p for p in paths if path_is_under_raw(p)]
    if not bad:
        return
    print('refuse: --in-place cannot write under raw/ — use --output-dir linted for raw charts:', file=sys.stderr)
    for p in bad:
        print(f'  {p}', file=sys.stderr)
    sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description='Nashville chart linter')
    parser.add_argument(
        'files', nargs='*',
        help='Chart files (default: raw/*.md matching <slug>_<bpm>.md)',
    )
    parser.add_argument(
        '--no-metadata', action='store_true',
        help='Skip filename/header checks (original key, new key, bpm, slug)',
    )
    parser.add_argument('--time-sig', type=int, default=4, help='Beats per bar (default: 4)')
    parser.add_argument('--warnings', '-w', action='store_true', help='Include warnings')
    parser.add_argument(
        '--output-dir', type=Path, metavar='DIR',
        help='Autofix charts and write results here (e.g. linted/). Never writes raw/.',
    )
    parser.add_argument(
        '--in-place', action='store_true',
        help='Autofix and overwrite each input file (same path/name). Cannot target raw/. '
        'Incompatible with --output-dir.',
    )
    args = parser.parse_args()

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = default_chart_paths()

    if not paths:
        print(
            'No chart files in raw/ matching <slug>_<bpm>.md — add charts under raw/ or pass paths.',
            file=sys.stderr,
        )
        sys.exit(2)

    out_dir = args.output_dir
    if out_dir is not None and args.in_place:
        print('use only one of --in-place or --output-dir', file=sys.stderr)
        sys.exit(2)
    if out_dir is not None:
        assert_output_dir_not_under_raw(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    if args.in_place:
        assert_in_place_targets_not_raw(paths)

    error_count = 0
    warn_count = 0
    written = 0
    for path in paths:
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            continue
        if out_dir is not None or args.in_place:
            text = path.read_text(encoding='utf-8')
            fixed = autofix_chart(text, path)
            diags = lint_file(
                path, time_sig=args.time_sig, metadata=not args.no_metadata, content=fixed,
            )
        else:
            diags = lint_file(path, time_sig=args.time_sig, metadata=not args.no_metadata)

        for d in diags:
            if d.severity == 'warning' and not args.warnings:
                continue
            print(d)
            if d.severity == 'error':
                error_count += 1
            else:
                warn_count += 1

        if out_dir is not None:
            (out_dir / linted_output_basename(path)).write_text(fixed, encoding='utf-8')
            written += 1
        elif args.in_place:
            path.write_text(fixed, encoding='utf-8')
            written += 1

    if error_count or warn_count:
        print(f"\n{error_count} error(s), {warn_count} warning(s)")
    else:
        print("No issues found.")

    if out_dir is not None:
        print(
            f"\n--output-dir {out_dir}: wrote {written} autofixed file(s) "
            f'(hyphenated names; raw/ unchanged)',
        )
    elif args.in_place:
        print(f'\n--in-place: overwrote {written} file(s) with autofix')

    sys.exit(1 if error_count else 0)


if __name__ == '__main__':
    main()
