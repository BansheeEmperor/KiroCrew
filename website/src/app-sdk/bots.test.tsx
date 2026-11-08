/**
 * The Bot hooks against a stubbed scoped API: the paths they call, the shapes they
 * hand back, the wake body (only `ref`), and a refusal surfaced as `error`.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBot, useBots, useWakeBot } from './index'

const ctx = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))

vi.mock('./scopedApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./scopedApi')>()),
  useCtx: () => ctx.current,
}))

const BOT = {
  id: 'code-reviewer',
  object: 'bot',
  name: 'Code Reviewer',
  description: 'reviews code',
  avatar_url: null,
  status: 'idle',
  has_goal: true,
}
const LIST = { object: 'list', data: [BOT], first_id: BOT.id, last_id: BOT.id, has_more: false }
const GOALS = { bot: BOT.id, goals: [{ id: 'g1', number: 1, text: 'ship it' }], key_results: [] }

function setApi(routes: Record<string, unknown>) {
  const get = vi.fn(async (path: string) => {
    if (path in routes) {
      const value = routes[path]
      if (value instanceof Error) throw value
      return value
    }
    throw new Error(`API 404: ${path}`)
  })
  const post = vi.fn(async () => ({ status: 'started' }))
  ctx.current = { api: { get, post } }
  return { get, post }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useBots', () => {
  it('lists the Bots from the member roster route', async () => {
    const { get } = setApi({ '/api/members': LIST })
    const { result } = renderHook(() => useBots())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual([BOT])
    expect(get).toHaveBeenCalledWith('/api/members', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it('surfaces a refusal as an error', async () => {
    setApi({ '/api/members': new Error('API 404: {"code":"not_found"}') })
    const { result } = renderHook(() => useBots())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toBeUndefined()
    expect(result.current.error?.message).toContain('404')
  })
})

describe('useBot', () => {
  it('reads one Bot and its goals by id', async () => {
    const { get } = setApi({ '/api/members': LIST, '/api/members/code-reviewer/goals': GOALS })
    const { result } = renderHook(() => useBot('code-reviewer'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual({ bot: BOT, goals: GOALS })
    expect(get).toHaveBeenCalledWith('/api/members/code-reviewer/goals', expect.anything())
  })

  it('reports a Bot the app was not given as an error', async () => {
    setApi({ '/api/members': LIST })
    const { result } = renderHook(() => useBot('someone-else'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toBeUndefined()
    expect(result.current.error).toBeInstanceOf(Error)
  })
})

describe('useWakeBot', () => {
  it('posts only the ref to the wake route', async () => {
    const { post } = setApi({})
    const { result } = renderHook(() => useWakeBot())
    let out: unknown
    await act(async () => { out = await result.current('code-reviewer', 'msg-1') })
    expect(out).toEqual({ status: 'started' })
    expect(post).toHaveBeenCalledWith('/api/members/code-reviewer/wake', { ref: 'msg-1' })
  })
})
