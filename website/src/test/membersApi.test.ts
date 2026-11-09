/**
 * `api.members`'s one benign denial: the 403 `owner_only` a caller that is not the dashboard
 * owner (a Shared Crew guest, a presigned link) gets. The left dock reads the roster on every
 * page and treats that refusal as "no mates to dock", so it must not reach the error journal.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { api, ApiError } from '../api/client'
import { recentErrors, __resetErrorJournalForTests } from '../utils/errorReport'

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  __resetErrorJournalForTests()
})

const failing = (status: number, body: Record<string, unknown>) =>
  ({
    ok: false,
    status,
    url: '/v1/crew/owner/members',
    headers: { get: () => null },
    text: async () => JSON.stringify(body),
  }) as unknown as Response

describe('api.members', () => {
  it('reads the roster from the Crew API Owner route', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, headers: { get: () => null }, json: async () => ({ members: [] }) })
    await expect(api.members()).resolves.toEqual({ members: [] })
    expect(String(fetchMock.mock.calls[0][0])).toBe('/v1/crew/owner/members')
  })

  it('reads the code of a Crew API error body: owner_only stays out of the journal', async () => {
    fetchMock.mockResolvedValue(failing(403, {
      error: { type: 'permission_error', code: 'owner_only', message: 'owner authorization required', param: null, request_id: 'req_1' },
    }))
    const err = await api.members().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toBe('owner authorization required')
    expect(recentErrors()).toHaveLength(0)
  })

  it('does NOT journal the 403 owner_only, but still throws it to the caller', async () => {
    fetchMock.mockResolvedValue(failing(403, { error: 'owner authorization required', code: 'owner_only' }))
    await expect(api.members()).rejects.toBeInstanceOf(ApiError)
    expect(recentErrors()).toHaveLength(0)
  })

  it('DOES journal any other failure, a 403 without that code included', async () => {
    fetchMock.mockResolvedValue(failing(500, { error: 'boom', code: 'internal' }))
    await expect(api.members()).rejects.toBeInstanceOf(ApiError)
    fetchMock.mockResolvedValue(failing(403, { error: 'forbidden' }))
    await expect(api.members()).rejects.toBeInstanceOf(ApiError)
    expect(recentErrors().map((e) => e.status)).toEqual(expect.arrayContaining([500, 403]))
    expect(recentErrors()).toHaveLength(2)
  })
})
