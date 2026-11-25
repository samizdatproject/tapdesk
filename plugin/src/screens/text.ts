// Width model for the glasses font, which is proportional. One unit is
// roughly an average lowercase letter, about 10 px. Calibrated against
// simulator screenshots: a 568 px text line holds about 56 units; "W" and CJK
// characters are much wider than the average.

const NARROW = new Set(" ilj.,:;'!|I`·")
const SEMI = new Set('frt()[]{}-"/')
const WIDE = new Set('mw')
const WIDEST = new Set('MW@%')

export function charWidth(ch: string): number {
  if (NARROW.has(ch)) return 0.6
  if (SEMI.has(ch)) return 0.8
  if (WIDE.has(ch)) return 1.35
  if (WIDEST.has(ch)) return 1.6
  if (/[A-Z0-9]/.test(ch)) return 1.2
  const cp = ch.codePointAt(0) ?? 0
  if (cp < 0x80) return 1
  if (cp <= 0x04ff) return 1.15 // Latin-1, extended Latin, Greek, Cyrillic
  if (cp === 0x2026) return 1 // …
  if (cp >= 0x2500 && cp <= 0x25ff) return 1.4 // box drawing, blocks, shapes
  return 2.3 // CJK (renders, ~22 px), emoji (dropped by the font; stay conservative)
}

export function textWidth(text: string): number {
  let w = 0
  for (const ch of text) w += charWidth(ch)
  return w
}

/**
 * Units that safely fit one line of a full-width container. Kept about 8%
 * under the measured 56 so our explicit line breaks always win over the
 * firmware's own wrapping.
 */
export const LINE_UNITS = 52

/** Cuts to `units`, ending in "…" when cut. */
export function fitWidth(text: string, units: number): string {
  if (textWidth(text) <= units) return text
  const room = units - charWidth('…')
  let w = 0
  let out = ''
  for (const ch of text) {
    const cw = charWidth(ch)
    if (w + cw > room) break
    out += ch
    w += cw
  }
  return out.trimEnd() + '…'
}

/** Greedy word wrap into lines of at most `units`; overlong words are split. */
export function wrapText(text: string, units = LINE_UNITS): string[] {
  const lines: string[] = []
  for (const para of text.replace(/\r/g, '').split('\n')) {
    let line = ''
    let lineW = 0
    for (const word of para.split(/\s+/).filter(Boolean)) {
      const wordW = textWidth(word)
      const gap = line ? charWidth(' ') : 0
      if (lineW + gap + wordW <= units) {
        line += (line ? ' ' : '') + word
        lineW += gap + wordW
        continue
      }
      if (line) lines.push(line)
      line = ''
      lineW = 0
      for (const ch of word) {
        const cw = charWidth(ch)
        if (lineW + cw > units) {
          lines.push(line)
          line = ''
          lineW = 0
        }
        line += ch
        lineW += cw
      }
    }
    lines.push(line)
  }
  // Drop blank lines at the end; keep ones between paragraphs.
  while (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  return lines
}
