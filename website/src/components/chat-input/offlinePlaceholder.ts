import { useAppSelector } from '../../store'
import { i18nT } from '../../i18n/t'

/* The composer's offline placeholder. It names the same cause as the shell's
   offline strip (offlineReason in shell/appShell.tsx): a relay that ended the
   session or refused it is not "will not send, reconnecting", and saying so
   would contradict the strip. A plain drop keeps the composer's own wording.
   The cause is read only while the gateway itself is down: the composer's own
   `connected` can be a crew window's tunnel (CrewChatWindow), which the
   relay's cause says nothing about. */
export function useOfflinePlaceholder(): string {
  const offlineCause = useAppSelector(s => (s.dashboard.connected ? null : s.dashboard.offlineCause))
  if (offlineCause === 'session_ended') return i18nT('app.relay_session_ended')
  if (offlineCause === 'refused') return i18nT('app.relay_access_refused')
  return i18nT('components.chatInput.gateway_offline_message_will_not_send')
}
