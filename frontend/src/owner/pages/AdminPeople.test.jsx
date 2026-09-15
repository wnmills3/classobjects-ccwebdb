import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listUsers: vi.fn(),
    createUser: vi.fn(),
    listCustomers: vi.fn(),
    updateUser: vi.fn(),
    setUserPassword: vi.fn(),
    updateCustomer: vi.fn(),
    addCustomerAddress: vi.fn(),
  },
}))

import { api } from '../api'
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

  async function openNewAccount() {
    const user = userEvent.setup()
    renderWithProviders(<AdminPeople />, { auth: adminAuth() })
    await screen.findByText('staff@example.com')
    await user.click(screen.getByRole('button', { name: 'New account' }))
    return user
  }

  it('creates an account from the New account form', async () => {
    api.createUser.mockResolvedValue({ id: 9, email: 'colleague@example.com' })
    const user = await openNewAccount()

    await user.type(screen.getByLabelText(/^email$/i), 'colleague@example.com')
    await user.type(screen.getByLabelText(/^name$/i), 'A Colleague')
    await user.selectOptions(screen.getByLabelText(/^role$/i), 'admin')
    await user.type(screen.getByLabelText(/initial password/i), 'long-enough-pw')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(api.createUser).toHaveBeenCalledWith({
      email: 'colleague@example.com',
      full_name: 'A Colleague',
      role: 'admin',
      password: 'long-enough-pw',
    })
    expect(await screen.findByText(/colleague@example.com/)).toBeInTheDocument()
    // The list is fetched again, so the new account appears in it.
    expect(api.listUsers).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('button', { name: 'Create account' })).toBeNull()
  })

  it('offers customer as the role until an administrator is chosen', async () => {
    await openNewAccount()
    expect(screen.getByLabelText(/^role$/i)).toHaveValue('customer')
  })

  it('keeps what was typed when the server refuses', async () => {
    api.createUser.mockRejectedValue(
      new Error('An account with that email already exists'),
    )
    const user = await openNewAccount()

    await user.type(screen.getByLabelText(/^email$/i), 'staff@example.com')
    await user.type(screen.getByLabelText(/initial password/i), 'long-enough-pw')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(
      await screen.findByText('An account with that email already exists'),
    ).toBeInTheDocument()
    // Shown in the form, not in place of the page: the table and the typed
    // details are both still there to correct and resend.
    expect(screen.getByLabelText(/^email$/i)).toHaveValue('staff@example.com')
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('closes the form on Cancel without creating anything', async () => {
    const user = await openNewAccount()
    await user.type(screen.getByLabelText(/^email$/i), 'nobody@example.com')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByLabelText(/^email$/i)).toBeNull()
    expect(api.createUser).not.toHaveBeenCalled()
  })

  it('reports a failed load', async () => {
    api.listUsers.mockRejectedValue(new Error('cannot reach the server'))
    renderWithProviders(<AdminPeople />, { auth: adminAuth() })
    expect(await screen.findByText('cannot reach the server')).toBeInTheDocument()
  })
})
