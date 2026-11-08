import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor, cleanup } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cachePreviewFlag, PREVIEW_CHAT_PAGE_COLORS } from '../../utils/previewFlags'
import { useChatPageTheme } from './useChatPageTheme'

const memberDashboard = vi.fn()
vi.mock('../../api/client', () => ({
  api: { memberDashboard: (...a: unknown[]) => memberDashboard(...a) },
}))

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

beforeEach(() => {
  memberDashboard.mockReset()
  cachePreviewFlag(PREVIEW_CHAT_PAGE_COLORS, false)
})
afterEach(() => cleanup())

describe('useChatPageTheme', () => {
  it('returns {} and fetches nothing while the preview is off — today\'s chat, unchanged', async () => {
    memberDashboard.mockResolvedValue({ page_theme: { '--bg': '#101418' } })
    const { result } = renderHook(() => useChatPageTheme('slug', 'Bot'), { wrapper: wrap() })
    expect(result.current).toEqual({})
    // The query is disabled, so the read is never issued.
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(memberDashboard).not.toHaveBeenCalled()
  })

  it('derives readable chat vars from the page theme once the preview is on', async () => {
    cachePreviewFlag(PREVIEW_CHAT_PAGE_COLORS, true)
    memberDashboard.mockResolvedValue({ page_theme: { '--bg': '#101418', '--accent': '#7c3aed' } })
    const { result } = renderHook(() => useChatPageTheme('slug', 'Bot'), { wrapper: wrap() })
    await waitFor(() => expect((result.current as Record<string, string>)['--bg']).toBe('#101418'))
    expect(memberDashboard).toHaveBeenCalledWith('slug', 'Bot')
    const vars = result.current as Record<string, string>
    expect(vars['--text']).toBe('#ffffff')
    expect(vars['--accent']).toBe('#7c3aed')
  })

  it('is {} with the preview on but no page colours', async () => {
    cachePreviewFlag(PREVIEW_CHAT_PAGE_COLORS, true)
    memberDashboard.mockResolvedValue({ state: 'live' })
    const { result } = renderHook(() => useChatPageTheme('slug', 'Bot'), { wrapper: wrap() })
    await waitFor(() => expect(memberDashboard).toHaveBeenCalled())
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(result.current).toEqual({})
  })
})
