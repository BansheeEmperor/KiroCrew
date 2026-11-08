/**
 * The Bot hooks: list the owner's Bots this app was given, read one Bot's goals,
 * and wake one.
 *
 * What an app may do is decided by the gateway, twice over:
 *
 * - the manifest declares `permissions.bots` (`"read"`, `"wake"`) and, for the
 *   path gate every app request passes, `"/api/members"` in `permissions.api`;
 * - the owner ticks which Bots the app may reach, in the app's settings page.
 *   A Bot that is not ticked answers 404 to every call, like one that does not
 *   exist. A fresh install reaches no Bot.
 *
 * Neither scope reaches a Bot's chat thread, briefing or rules, and a wake carries
 * no text from the app: the gateway writes the line the Bot reads.
 *
 * Wire mapping. Today the hooks call the gateway's member routes; the Crew API
 * draft names the same reads `/v1/crew/bots`. Only the paths below change when the
 * hooks move there; the shapes are the draft's `Bot`, `BotList` and `BotGoals`.
 *
 *   useBots()          GET  /api/members               ->  GET  /v1/crew/bots
 *   useBot(id)         GET  /api/members/{id}/goals    ->  GET  /v1/crew/bots/{id}/goals
 *   useWakeBot()(id,r) POST /api/members/{id}/wake     ->  (not in the draft yet)
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useCtx } from './scopedApi'

/** One Bot, as the Crew API draft's `Bot` (plus `status` and `has_goal`). */
export interface Bot {
  /** The Bot's member slug; every Bot route is keyed on it. */
  id: string
  object: 'bot'
  name: string
  description: string
  avatar_url: string | null
  /** `busy` while a turn runs in the Bot's thread. */
  status: 'busy' | 'idle'
  has_goal: boolean
}

/** The draft's `ListEnvelope` of `Bot`. */
export interface BotList {
  object: 'list'
  data: Bot[]
  first_id: string | null
  last_id: string | null
  has_more: boolean
}

/** A key result, as the draft's `KeyResult`. */
export interface BotKeyResult {
  id: string
  text: string
  target: string
  current: string | null
  /** Unix seconds of the last reading, or null. */
  updated_at: number | null
  goal_ids: string[]
  history: Array<{ at: number; value: string }>
}

/** One Bot's goals and key results, as the draft's `BotGoals`. */
export interface BotGoals {
  bot: string
  goals: Array<{ id: string; number: number; text: string }>
  key_results: BotKeyResult[]
}

/** What a Bot read hook returns. `error` keeps the last failure until a reload succeeds. */
export interface BotQuery<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
  /** Read again now. */
  reload: () => void
}

/** How a wake was taken: run now, or kept until the Bot's current turn ends. */
export interface BotWakeResult {
  status: 'started' | 'queued'
}

export const BOTS_PATH = '/api/members'
export const botGoalsPath = (id: string) => `${BOTS_PATH}/${encodeURIComponent(id)}/goals`
export const botWakePath = (id: string) => `${BOTS_PATH}/${encodeURIComponent(id)}/wake`

function useBotRead<T>(path: string | null): BotQuery<T> {
  const { api } = useCtx()
  const [data, setData] = useState<T | undefined>(undefined)
  const [error, setError] = useState<Error | undefined>(undefined)
  const [loading, setLoading] = useState(path !== null)
  const [generation, setGeneration] = useState(0)
  const reload = useCallback(() => setGeneration(g => g + 1), [])
  const latest = useRef(0)

  useEffect(() => {
    if (path === null) return
    const run = ++latest.current
    const controller = new AbortController()
    setLoading(true)
    api.get<T>(path, { signal: controller.signal }).then(
      body => {
        if (run !== latest.current) return
        setData(body)
        setError(undefined)
        setLoading(false)
      },
      (err: unknown) => {
        if (run !== latest.current || controller.signal.aborted) return
        setError(err instanceof Error ? err : new Error(String(err)))
        setLoading(false)
      },
    )
    return () => controller.abort()
  }, [api, path, generation])

  return { data, error, loading, reload }
}

/** The Bots the owner gave this app, in the owner's order. Needs `permissions.bots: ["read"]`. */
export function useBots(): BotQuery<Bot[]> {
  const list = useBotRead<BotList>(BOTS_PATH)
  return { ...list, data: list.data?.data }
}

/**
 * One Bot and its goals. `bot` is undefined while loading, and when the id is
 * not one this app was given (the goals read then fails with a 404).
 * Needs `permissions.bots: ["read"]`.
 */
export function useBot(id: string): BotQuery<{ bot: Bot | undefined; goals: BotGoals }> {
  const list = useBotRead<BotList>(id ? BOTS_PATH : null)
  const goals = useBotRead<BotGoals>(id ? botGoalsPath(id) : null)
  const { reload: reloadList } = list
  const { reload: reloadGoals } = goals
  const reload = useCallback(() => {
    reloadList()
    reloadGoals()
  }, [reloadList, reloadGoals])
  return {
    data: goals.data ? { bot: list.data?.data.find(b => b.id === id), goals: goals.data } : undefined,
    error: goals.error ?? list.error,
    loading: list.loading || goals.loading,
    reload,
  }
}

/**
 * Returns `wakeBot(id, ref)`: tell a Bot it has a new message `#ref` in this app.
 *
 * `ref` is an opaque id the app minted (1-64 of `A-Z a-z 0-9 _ . : -`), never
 * text: the Bot reads the gateway's own line naming this app and the ref, and
 * fetches the message through the app's tools if it has them. A 429 means the
 * per-app, per-Bot limit was reached; retry after the `Retry-After` seconds.
 * Needs `permissions.bots: ["wake"]`.
 */
export function useWakeBot(): (id: string, ref: string) => Promise<BotWakeResult> {
  const { api } = useCtx()
  return useCallback(
    (id: string, ref: string) => api.post<BotWakeResult>(botWakePath(id), { ref }),
    [api],
  )
}
