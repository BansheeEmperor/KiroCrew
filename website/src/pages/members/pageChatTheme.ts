/**
 * Map a crewmate's DASHBOARD PAGE colour tokens onto the CHAT column's CSS
 * variables (#20635), so the chat and the Dashboard tab beside it read as one
 * theme.
 *
 * The problem this solves: a crewmate's page can declare its own colours
 * (`theme.tokens`, validated in `artifact_store/dashboard_package.py`), but the
 * chat in the middle stays on the app theme — a strongly coloured page and a
 * default-dark chat look like two different apps. This module takes the two
 * page colours the chat can safely follow and returns the variable overrides
 * the column sets inline.
 *
 * WHY ONLY TWO TOKENS, NOT THE PAGE'S WHOLE PALETTE: a page's tokens are
 * free-form custom-property names a page author picks (up to 64 of them), with
 * no guaranteed meaning. Copying arbitrary names onto the chat's own variables
 * would be guessing. The chat follows exactly the two whose role is
 * unambiguous — the page BACKGROUND (`--bg`) and the page ACCENT (`--accent`),
 * the same names the app's own theme uses — and derives every text colour
 * itself. A page that declares neither keeps today's chat untouched.
 *
 * WHY TEXT IS DERIVED, NOT READ: readability cannot be left to a page author.
 * A declared text colour could sit at any contrast on the declared background.
 * So the body/accent text colours are chosen here by measured contrast
 * (`apps/crew-companion/contrast.ts`, the repo's one WCAG toolkit): black or
 * white, whichever has more contrast on that background. Against any sRGB
 * colour the better of pure black and pure white never drops below ~4.58:1, so
 * this clears 4.5:1 on a light page and a dark one alike. The >= 4.5 guard
 * below is therefore a belt-and-braces check that only ever fires for an
 * UNPARSEABLE background, where it falls back to today's look.
 */
import { contrastRatio, parseCssColor, type Rgb } from '../../apps/crew-companion/contrast'

/**
 * The page colour tokens the chat follows, keyed by CSS custom-property name as
 * the server's `page_theme` emits them. Free-form elsewhere; the chat reads
 * exactly these two.
 */
export interface PageColorTokens {
  /** The page's `--bg`: the surface the chat paints its background from. */
  '--bg'?: string
  /** The page's `--accent`: the chat's accent (caret, links, send). */
  '--accent'?: string
}

/** The chat column's CSS-variable overrides, ready to spread onto an element's style. */
export type ChatThemeVars = Record<string, string>

const BLACK: Rgb = { r: 0, g: 0, b: 0, a: 1 }
const WHITE: Rgb = { r: 255, g: 255, b: 255, a: 1 }

/** WCAG AA for normal text; stated here so the contract is visible at the call site. */
export const CHAT_AA_TEXT = 4.5

/**
 * The readable text colour for *bg*: black or white, whichever has the higher
 * contrast. Returns the ratio too, so a caller can refuse a background whose
 * best text still falls short. `null` when *bg* does not parse — an unknown
 * colour is treated as unusable rather than guessed.
 */
export function readableText(bg: string): { color: string; ratio: number } | null {
  const back = parseCssColor(bg)
  if (!back) return null
  const onBlack = contrastRatio(BLACK, back)
  const onWhite = contrastRatio(WHITE, back)
  return onWhite >= onBlack
    ? { color: '#ffffff', ratio: onWhite }
    : { color: '#000000', ratio: onBlack }
}

/**
 * The chat column's variable overrides for a crewmate's page colours, or an
 * EMPTY object when the page drives nothing (no tokens, or a background whose
 * best text cannot clear 4.5:1 — the fail-safe is today's look, never
 * unreadable text).
 *
 * An empty result is the default-OFF state expressed as data: the column
 * spreads `{}`, which adds no inline variable, so the chat is byte-for-byte the
 * one on `main`.
 */
export function chatThemeVars(tokens: PageColorTokens | null | undefined): ChatThemeVars {
  if (!tokens) return {}
  const vars: ChatThemeVars = {}
  const bg = tokens['--bg']
  const accent = tokens['--accent']

  if (bg) {
    const text = readableText(bg)
    // Only paint the background when its derived text is actually readable;
    // otherwise leave the chat on its own theme rather than show poor contrast.
    if (text && text.ratio >= CHAT_AA_TEXT) {
      vars['--bg'] = bg
      vars['--text'] = text.color
      // Message bubbles sit on `--card`/`--card-fg`; keep them on the same
      // surface so a bubble is not an untinted island on a tinted chat.
      vars['--card'] = bg
      vars['--card-fg'] = text.color
    }
  }

  if (accent) {
    const text = readableText(accent)
    // The accent is a background for its own label (send button, pills), so it
    // is paired with a readable foreground the same way.
    if (text && text.ratio >= CHAT_AA_TEXT) {
      vars['--accent'] = accent
      vars['--accent-fg'] = text.color
    }
  }

  return vars
}
