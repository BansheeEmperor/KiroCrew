import { describe, expect, it } from 'vitest'
import { contrastRatio, parseCssColor } from '../../apps/crew-companion/contrast'
import { CHAT_AA_TEXT, chatThemeVars, readableText } from './pageChatTheme'

/** The measured contrast of a `#rrggbb` text colour on a `#rrggbb` background. */
function ratio(fg: string, bg: string): number {
  const f = parseCssColor(fg)!
  const b = parseCssColor(bg)!
  return contrastRatio(f, b)
}

describe('readableText', () => {
  it('picks white on a dark background and black on a light one', () => {
    expect(readableText('#101418')?.color).toBe('#ffffff')
    expect(readableText('#f3f4f6')?.color).toBe('#000000')
  })

  it('reports the contrast of its chosen text, >= 4.5 on both ends', () => {
    const dark = readableText('#101418')!
    const light = readableText('#f3f4f6')!
    expect(dark.ratio).toBeGreaterThanOrEqual(CHAT_AA_TEXT)
    expect(light.ratio).toBeGreaterThanOrEqual(CHAT_AA_TEXT)
    // The reported ratio is the real one for the returned colour.
    expect(dark.ratio).toBeCloseTo(ratio(dark.color, '#101418'), 5)
  })

  it('returns null for an unparseable colour rather than guessing', () => {
    expect(readableText('rebeccapurple')).toBeNull()
    expect(readableText('')).toBeNull()
  })
})

describe('chatThemeVars', () => {
  it('is empty with no tokens — the default-off, byte-for-byte state', () => {
    expect(chatThemeVars(null)).toEqual({})
    expect(chatThemeVars(undefined)).toEqual({})
    expect(chatThemeVars({})).toEqual({})
  })

  it('pairs every emitted background with a text colour that clears 4.5:1', () => {
    // A strongly coloured dark page, light mode on the Dashboard tab mirrored.
    for (const bg of ['#101418', '#f3f4f6', '#1e3a8a', '#fef3c7']) {
      const vars = chatThemeVars({ '--bg': bg })
      expect(vars['--bg']).toBe(bg)
      expect(ratio(vars['--text'], vars['--bg'])).toBeGreaterThanOrEqual(CHAT_AA_TEXT)
      // Bubbles ride the same surface, paired the same way.
      expect(ratio(vars['--card-fg'], vars['--card'])).toBeGreaterThanOrEqual(CHAT_AA_TEXT)
    }
  })

  it('pairs the accent with a readable label colour', () => {
    const vars = chatThemeVars({ '--accent': '#7c3aed' })
    expect(vars['--accent']).toBe('#7c3aed')
    expect(ratio(vars['--accent-fg'], vars['--accent'])).toBeGreaterThanOrEqual(CHAT_AA_TEXT)
  })

  it('derives >= 4.5:1 text for every sRGB background (the worst case still clears)', () => {
    // Black-or-white against any sRGB colour never drops below ~4.58:1, so the
    // chat always gets readable text. `#8855ee` is the measured worst case.
    for (const bg of ['#8855ee', '#767676', '#008080', '#9c6b2f']) {
      const vars = chatThemeVars({ '--bg': bg })
      expect(vars['--bg']).toBe(bg)
      expect(ratio(vars['--text'], vars['--bg'])).toBeGreaterThanOrEqual(CHAT_AA_TEXT)
    }
  })

  it('ignores an unparseable token instead of applying it', () => {
    expect(chatThemeVars({ '--bg': 'var(--something)' })).toEqual({})
  })
})
