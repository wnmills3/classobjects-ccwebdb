import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import Login from './Login'
import { anonymousAuth, renderWithProviders } from '../../test/helpers'

describe('Login', () => {
  it('renders the sign-in form', () => {
    renderWithProviders(<Login />)
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('offers registration to someone without an account', () => {
    renderWithProviders(<Login />)
    expect(
      screen.getByRole('link', { name: /register as a customer/i }),
    ).toHaveAttribute('href', '/register')
  })

  it('submits the typed credentials', async () => {
    const user = userEvent.setup()
    const login = vi.fn().mockResolvedValue(undefined)
    renderWithProviders(<Login />, { auth: anonymousAuth({ login }) })

    await user.type(screen.getByLabelText(/email/i), 'buyer@example.com')
    await user.type(screen.getByLabelText(/password/i), 'hunter2')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() =>
      expect(login).toHaveBeenCalledWith('buyer@example.com', 'hunter2'),
    )
  })

  it('shows the failure reason and leaves the form usable', async () => {
    const user = userEvent.setup()
    const login = vi.fn().mockRejectedValue(new Error('Incorrect email or password'))
    renderWithProviders(<Login />, { auth: anonymousAuth({ login }) })

    await user.type(screen.getByLabelText(/email/i), 'buyer@example.com')
    await user.type(screen.getByLabelText(/password/i), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Incorrect email or password')).toBeInTheDocument()
    // The button must come back out of its busy state, or a mistyped password
    // locks the user out of retrying.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled(),
    )
  })
})
