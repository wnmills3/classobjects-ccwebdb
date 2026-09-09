import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../shared/api', () => ({
  api: {
    listUsers: vi.fn(),
    listCustomers: vi.fn(),
    updateUser: vi.fn(),
    setUserPassword: vi.fn(),
    updateCustomer: vi.fn(),
    addCustomerAddress: vi.fn(),
  },
}))

import { api } from '../../shared/api'
import AdminPeople from './AdminPeople'
import { adminAuth, renderWithProviders } from '../../test/helpers'

beforeEach(() => {
  vi.clearAllMocks()
  api.listUsers.mockResolvedValue([
    {
      id: 1,
      email: 'staff@example.com',
      full_name: 'Staff',
      role: 'admin',
      is_active: true,
    },
  ])
  api.listCustomers.mockResolvedValue([
    { id: 5, full_name: 'Ada Lovelace', email: 'ada@example.com', addresses: [] },
  ])
})

describe('AdminPeople', () => {
  it('opens on the accounts tab and lists users', async () => {
    renderWithProviders(<AdminPeople />, { auth: adminAuth() })
    expect(await screen.findByText('staff@example.com')).toBeInTheDocument()
  })

  it('switches to customers', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AdminPeople />, { auth: adminAuth() })
    await screen.findByText('staff@example.com')

    await user.click(screen.getByRole('button', { name: 'Customers' }))
    // The customers table identifies a row by email, not by name.
    expect(await screen.findByText('ada@example.com')).toBeInTheDocument()
  })

  it('reveals the address form for one customer on request', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AdminPeople />, { auth: adminAuth() })
    await screen.findByText('staff@example.com')

    await user.click(screen.getByRole('button', { name: 'Customers' }))
    await user.click(await screen.findByRole('button', { name: /new address/i }))

    // Every field of the address form, which is only mounted once asked for.
    expect(await screen.findByLabelText(/address line 1/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/^line 2$/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/^city$/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/state \/ region/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/postal code/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/^country$/i)).toBeInTheDocument()
  })

  it('reports a failed load', async () => {
    api.listUsers.mockRejectedValue(new Error('cannot reach the server'))
    renderWithProviders(<AdminPeople />, { auth: adminAuth() })
    expect(await screen.findByText('cannot reach the server')).toBeInTheDocument()
  })
})
