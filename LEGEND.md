# Notation Legend

## Time Signature
4/4 (assumed unless stated)

## Bar Structure
- `[` = bar start
- Adjacent chords within bar separated by spaces

## Scale Degrees
- `1, 2, 3, 4, 5, 6, 7` = scale degree numbers
- `b7`, `#4`, etc. = flat/sharp modifiers precede the degree

## Chord Quality (suffixes)
- `[none]` = major triad
- `m` = minor (e.g. `2m`)
- `D` = dominant 7 (e.g. `5D`, `1D`)
- `M` = major 7 (e.g. `1M`)
- `ø` = half diminished (e.g. `7ø`)
- `9`, `11`, etc. = extensions (include 7th)
- `add9`, `add11`, etc. = added tones (no 7th)

## Compound suffixes
Suffixes **chain** after the degree: read left to right, same idea as stacked chord symbols in letter notation.

- `m7` = minor 7th on that degree (e.g. `2m7` = “minor-seven built on scale degree 2”)
- `D9`, `D11`, `D#9`, … = dominant family + alteration/extension on that degree (e.g. `5D11`, `6D#9`)
- `mb5` / `7mb5` = diminished 5 / half-diminished on that degree (context in the bar)
- `^` = **major triad** on that degree (e.g. `1^`). Not major 7 — use `M` for maj7.
- `sus`, `sus/1`, … = suspended (e.g. `2sus` = sus voicing on degree 2)

## Numbers ↔ letter chords (same harmony)
**Letters are not in the chart.** They follow the **key** stated at the top of each song (major or minor as written). The numbers are **relative to that key**.

### Example: ii–V–I in **C major**
Same progression as **Dm7 → G7 → C**:

| This repo | In C major |
|-----------|------------|
| `2m7` | Dm7 |
| `5D` | G7 |
| `1` | C (major triad) |

Example bar line (one chord per bar, 4 beats each):

```text
[2m7... [5D... [1...
```

### Example: same numbers, different key
In **G major**, the same **symbols** `2m7`–`5D`–`1` refer to **Am7 → D7 → G**, not Dm7–G7–C. The **numbers** stay tied to the key; only the letter names change.

## Slash Chords
- `/[degree]` = alternate bass note (scale degree)
- Example: `1/3` = 1 chord with 3rd scale degree in bass
- Example: `b7/1` = b7 chord with root note of key in bass

## Duration
- Chord lasts 1 beat by default
- `.` = extend current chord by 1 beat
- `..` = extend current chord by 2 beats
- `...` = extend current chord by 3 beats
- Chord continues until new scale degree or rest

### Examples
- `1` = 1 beat
- `1...` = 4 beats
- `4. 5` = 2 beats of 4, 1 beat of 5
- `[1M.4M.` = 2 beats of 1M, 2 beats of 4M

## Repeat
- `%` = reuse chord symbol from previous bar (apply whatever duration follows)
- Example: `[%...` = 4 beats of the previous bar's chord

## Rests
- `-` = 1 beat rest (stops current chord)
- `--` = 2 beat rest
- `-.` = 1 beat rest, 1 beat chord continuation
- Example: `[%-..` = 1 beat chord, 1 beat rest, 2 beats chord

## Compound Bars
- `[4.1/3 2m` = 2 beats of 4, 1 beat of 1/3, 1 beat of 2m
- `[%-..` = 1 beat % chord, 1 beat rest, 2 beats % chord
- `[1m.5D/7.` = 2 beats of 1m, 2 beats of 5D/7

## Instructions
- `(text)` on same line = performance note for that bar
- Example: `[1D... [%... (boogie 6-7)`
