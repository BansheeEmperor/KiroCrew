import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { CSSProperties } from 'react'
import { api } from '../../api/client'
import { retryPolicy } from '../../api/queryClient'
import { MEMBER_DASHBOARD_QUERY_PREFIX } from '../../hooks/useWebSocket'
import { useChatPageColors } from '../../hooks/usePreviewFlag'
import { chatThemeVars } from './pageChatTheme'

/**
 * The CHAT column's colour overrides for the open crewmate, from that
 * crewmate's dashboard PAGE (#20635).
 *
 * Returns a style object of CSS-variable overrides to spread onto the thread
 * column, or `{}` when nothing applies. Empty is the default-OFF, byte-for-byte
 * state: the column spreads `{}`, which sets no variable, so the chat is the
 * one on `main`.
 *
 * Gated three ways, each of which yields `{}`:
 *   - the crewmate-chat-page-colours preview is off (the query does not even
 *     run — `enabled` is false — so a resting dashboard fetches nothing extra);
 *   - the page declares none of the colour tokens the chat follows;
 *   - a declared background whose derived text cannot be made readable.
 *
 * Shares the member-dashboard read's query key with the Dashboard tab, so when
 * both are mounted there is one fetch and one cache entry, and a page the
 * crewmate changes, replaces or rolls back moves the chat the moment the tab's
 * read refreshes.
 */
export function useChatPageTheme(slug: string, member: string): CSSProperties {
  const on = useChatPageColors()
  const enabled = on && Boolean(slug) && Boolean(member)
  const { data } = useQuery({
    queryKey: [MEMBER_DASHBOARD_QUERY_PREFIX, slug, member, 'chat-page-colors'],
    queryFn: () => api.memberDashboard(slug, member),
    enabled,
    retry: retryPolicy,
    // The page theme changes rarely; a stale read tinting the chat for a minute
    // is harmless and saves a fetch on every thread switch.
    staleTime: 60_000,
  })
  return useMemo(() => (enabled ? (chatThemeVars(data?.page_theme) as CSSProperties) : {}), [enabled, data?.page_theme])
}
