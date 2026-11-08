/**
 * The "Bots this app can reach" card: hidden for an app with no Bot scope, a
 * checkbox per Bot, a scope the owner has not approved marked, and Save storing
 * exactly the ticked list.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  members: vi.fn(),
  getAppBotGrants: vi.fn(),
  setAppBotGrants: vi.fn(),
}))
vi.mock('../api/client', () => ({ api }))

import AppDetailBotGrants from './AppDetailBotGrants'

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  api.members.mockResolvedValue({
    members: [
      { name: 'code-reviewer', slug: 'code-reviewer', display_name: 'Reviewer' },
      { name: 'writer', slug: 'writer' },
    ],
  })
  api.getAppBotGrants.mockResolvedValue({ bots: ['writer'], declaredScopes: ['read', 'wake'], scopes: ['read'] })
  api.setAppBotGrants.mockImplementation(async (_name: string, bots: string[]) => ({ bots, declaredScopes: ['read', 'wake'], scopes: ['read'] }))
})

describe('AppDetailBotGrants', () => {
  it('renders nothing for an app that declares no Bot scope', () => {
    const { container } = render(wrap(<AppDetailBotGrants appName="notes" declared={undefined} />))
    expect(container.textContent).toBe('')
    expect(api.getAppBotGrants).not.toHaveBeenCalled()
  })

  it('shows the ticked list, marks an unapproved scope, and saves the new ticks', async () => {
    render(wrap(<AppDetailBotGrants appName="notes" declared={['read', 'wake']} />))
    const writer = await screen.findByRole('checkbox', { name: 'writer' })
    await waitFor(() => expect(writer).toBeChecked())
    const reviewer = screen.getByRole('checkbox', { name: 'Reviewer' })
    expect(reviewer).not.toBeChecked()
    expect(screen.getAllByText(/not approved/)).toHaveLength(1)

    fireEvent.click(reviewer)
    fireEvent.click(screen.getByRole('button', { name: 'Save Bot access' }))
    await waitFor(() => expect(api.setAppBotGrants).toHaveBeenCalledWith('notes', ['writer', 'code-reviewer']))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
  })
})
