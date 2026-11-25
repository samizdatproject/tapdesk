import { describe, expect, it } from 'vitest'
import { LINE_UNITS, fitWidth, textWidth, wrapText } from './text'

describe('width model', () => {
  it('treats narrow, wide and non-Latin characters differently', () => {
    expect(textWidth('iiii')).toBeLessThan(textWidth('aaaa'))
    expect(textWidth('WWWW')).toBeGreaterThan(textWidth('aaaa'))
    expect(textWidth('漢字')).toBeCloseTo(4.6)
  })

  it('stays under the line limits seen in the simulator', () => {
    // The firmware wrapped 38 "W", 26 CJK characters and about 50 digits.
    expect(textWidth('W'.repeat(38))).toBeGreaterThan(LINE_UNITS)
    expect(textWidth('你'.repeat(25))).toBeGreaterThan(LINE_UNITS)
    expect(textWidth('0'.repeat(50))).toBeGreaterThan(LINE_UNITS)
  })
})

describe('wrapText', () => {
  it('wraps on words and keeps every line within the budget', () => {
    const text = 'Can you join the investor call at 3? Need you for the token-unlock slide, and bring the numbers.'
    const lines = wrapText(text)
    expect(lines.length).toBeGreaterThan(1)
    for (const l of lines) expect(textWidth(l)).toBeLessThanOrEqual(LINE_UNITS)
    expect(lines.join(' ')).toBe(text)
  })

  it('splits words longer than a line', () => {
    const lines = wrapText('x'.repeat(130), 52)
    expect(lines.map(l => l.length)).toEqual([52, 52, 26])
  })

  it('keeps paragraph breaks', () => {
    expect(wrapText('one\ntwo')).toEqual(['one', 'two'])
  })
})

describe('fitWidth', () => {
  it('cuts with an ellipsis only when needed', () => {
    expect(fitWidth('short', 52)).toBe('short')
    const cut = fitWidth('a'.repeat(80), 52)
    expect(cut.endsWith('…')).toBe(true)
    expect(textWidth(cut)).toBeLessThanOrEqual(52)
  })
})
