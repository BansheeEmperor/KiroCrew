// @vitest-environment jsdom
/**
 * Edit is offered only over a page whose bridge can answer it. The capture layer
 * covers the frame, so over a page that runs no script (or one rendered without the
 * bridge) it would swallow every click and scroll with nothing to answer them.
 */
import { describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import { api, type DashboardManifest } from '../../api/client'
import { renderWithProviders } from '../../test/helpers'
import CrewDynamicDashboard from './CrewDynamicDashboard'
import { EDIT_MESSAGE_TYPE } from './dashboardEdit'

vi.mock('../../hooks/useSandboxDoc', () => ({
  useSandboxDoc: (srcdoc: string | null) => ({
    url: srcdoc ? `/sandbox-doc/${srcdoc.length}` : null,
    pending: false,
    failed: false,
    stalled: false,
    retry: () => {},
  }),
}))
vi.mock('../../hooks/usePreviewFlag', () => ({ usePreviewFlag: () => true }))
vi.mock('../../hooks/usePreviewFlagServerSync', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../hooks/usePreviewFlagServerSync')>()),
  useServerPreviewFlag: () => true,
}))

const MANIFEST = { id: 'atlas-tree', version: 1, title: 'Tree', fields: [] } as unknown as DashboardManifest
const WITH_BRIDGE = `<!doctype html><p>v1</p><script>/* ${EDIT_MESSAGE_TYPE} */</script>`

function page(over: Record<string, unknown> = {}) {
  return {
    instance_version: 1,
    template: { id: 'atlas-tree', version: 1 },
    html: '<!doctype html><p>stored</p>',
    rendered_html: WITH_BRIDGE,
    manifest: MANIFEST,
    state: 'live' as const,
    ...over,
  }
}

/** How long a query may wait for the lazily imported edit layer to mount. */
const LAZY_LAYER_TIMEOUT_MS = 5000

const mount = () =>
  renderWithProviders(
    <CrewDynamicDashboard target={{ kind: 'member', slug: 'atlas', member: 'Atlas' }} displayName="Atlas" />,
  )

describe('the Edit button', () => {
  it('is offered over a page that runs script with the bridge in it', async () => {
    vi.spyOn(api, 'memberDashboard').mockResolvedValue(page())
    mount()
    const edit = await screen.findByTestId('crew-dashboard-edit')
    // Named for what the mode does first: inspect where values came from.
    expect(edit).toHaveTextContent('Inspect')
    // Shown at rest, not only on hover: every use of edit mode starts here.
    expect(edit.className).not.toMatch(/opacity-0/)
  })

  it('keeps the live/preview marker last in the read key with edit on', async () => {
    vi.spyOn(api, 'memberDashboard').mockResolvedValue(page())
    const { queryClient } = mount()
    await screen.findByTestId('crew-dashboard-edit')
    // The preview hand-over tells a staged read from a live one by the key's LAST
    // element, so the edit marker must not take that place.
    const keys = queryClient.getQueryCache().getAll().map(q => q.queryKey as unknown[]).filter(k => k.includes('edit'))
    expect(keys.length).toBeGreaterThan(0)
    for (const key of keys) expect(['live', 'preview']).toContain(key[key.length - 1])
  })

  it('is withdrawn while an older page is kept on screen for a newer version', async () => {
    const read = vi.spyOn(api, 'memberDashboard').mockResolvedValue(page())
    const { queryClient } = mount()
    await screen.findByTestId('crew-dashboard-edit')
    // A newer version arrives and waits on probation; the old page stays on screen.
    read.mockResolvedValue(page({ instance_version: 2, rendered_html: `${WITH_BRIDGE}<p>v2</p>` }))
    await act(async () => { await queryClient.refetchQueries({ predicate: q => q.queryKey.includes('live') }) })

    // The gateway traces against the newer version's manifest, so the old page on
    // screen cannot be inspected until the newer one is shown.
    await waitFor(() => expect(screen.queryByTestId('crew-dashboard-edit')).toBeNull())
  })

  it('keeps an open edit layer, and its drafts, when a newer version starts waiting', async () => {
    const read = vi.spyOn(api, 'memberDashboard').mockResolvedValue(page())
    const { queryClient } = mount()
    const edit = await screen.findByTestId('crew-dashboard-edit')
    act(() => { edit.click() })
    // Waits on the lazy import of CrewDashboardEditLayer, which a loaded worker can
    // take past the default 1 s to resolve.
    expect(await screen.findByTestId('dashboard-edit-capture', {}, { timeout: LAZY_LAYER_TIMEOUT_MS })).toBeInTheDocument()
    read.mockResolvedValue(page({ instance_version: 2, rendered_html: `${WITH_BRIDGE}<p>v2</p>` }))
    await act(async () => { await queryClient.refetchQueries({ predicate: q => q.queryKey.includes('live') }) })
    // Wait for the newer read to land in the cache: the candidate is then version 2.
    await waitFor(() => expect(
      queryClient.getQueryCache().getAll().some(q => (q.state.data as { instance_version?: number } | undefined)?.instance_version === 2),
    ).toBe(true))
    // The layer stays mounted: a draft typed in it lives in its own state.
    expect(screen.getByTestId('dashboard-edit-capture')).toBeInTheDocument()
  })

  it('is not offered when the page may not run script', async () => {
    vi.spyOn(api, 'memberDashboard').mockResolvedValue(page({ scripts: false }))
    mount()
    await screen.findByTestId('crew-dashboard-iframe')
    expect(screen.queryByTestId('crew-dashboard-edit')).toBeNull()
  })
})
