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
  // `display_name`, which is what `CustomerOut` actually returns. The fixture
  // said `full_name` -- a field no customer response has ever carried -- so
  // the name column rendered blank in every test here and nothing noticed.
  api.listCustomers.mockResolvedValue([
    { id: 5, display_name: 'Ada Lovelace', email: 'ada@example.com', addresses: [] },
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
    expect(await screen.findByText('ada@example.com')).toBeInTheDocument()
    // The name too. It used to be absent because the fixture named the field
    // wrongly, and the assertion was dropped rather than the fixture fixed.
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
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

  it('keeps the accounts table when an action is refused', async () => {
    // The last-administrator guard answers 409, and that refusal is about a
    // row. Replacing the page with it left the operator with an error and no
    // rows -- unable to see which account they had just tried to change, or
    // to act on the advice.
    api.updateUser.mockRejectedValue(
      new Error('That is the last administrator; promote another first.'),
    )
    const user = userEvent.setup()
    renderWithProviders(<AdminPeople />, { auth: adminAuth() })

    await user.click(await screen.findByRole('button', { name: 'Active' }))

    expect(
      await screen.findByText('That is the last administrator; promote another first.'),
    ).toBeInTheDocument()
    expect(screen.getByText('staff@example.com')).toBeInTheDocument()
  })

  it('keeps a customer edit on screen when saving it is refused', async () => {
    // `draft` holds what the operator typed. It is React state, so it
    // survives the failure -- but only if the input is still mounted. The
    // page used to unmount the whole table, taking the half-finished edit
    // with it and offering no way back.
    api.updateCustomer.mockRejectedValue(new Error('that email is already in use'))
    const user = userEvent.setup()
    renderWithProviders(<AdminPeople />, { auth: adminAuth() })

    await user.click(await screen.findByRole('button', { name: 'Customers' }))
    await user.click(await screen.findByRole('button', { name: 'Edit' }))

    const name = screen.getByDisplayValue('Ada Lovelace')
    await user.clear(name)
    await user.type(name, 'Ada King')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('that email is already in use')).toBeInTheDocument()
    // The typing is still there to be corrected, not retyped.
    expect(screen.getByDisplayValue('Ada King')).toBeInTheDocument()
  })
})
