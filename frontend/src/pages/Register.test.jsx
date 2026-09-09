import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import Register from './Register'
import { anonymousAuth, renderWithProviders } from '../test/helpers'

describe('Register', () => {
  it('renders the account form', () => {
    renderWithProviders(<Register />)
    expect(
      screen.getByRole('heading', { name: /create an account/i }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText(/full name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
  })

  it('submits every field it collected', async () => {
    const user = userEvent.setup()
    const register = vi.fn().mockResolvedValue(undefined)
    renderWithProviders(<Register />, { auth: anonymousAuth({ register }) })

    await user.type(screen.getByLabelText(/full name/i), 'Ada Lovelace')
    await user.type(screen.getByLabelText(/email/i), 'ada@example.com')
    await user.type(screen.getByLabelText(/password/i), 'analytical')
    await user.click(
      screen.getByRole('button', { name: /create account|register|sign up/i }),
    )

    await waitFor(() =>
      expect(register).toHaveBeenCalledWith({
        full_name: 'Ada Lovelace',
        email: 'ada@example.com',
        password: 'analytical',
      }),
    )
  })

  it('reports why the registration was refused', async () => {
    const user = userEvent.setup()
    const register = vi.fn().mockRejectedValue(new Error('email already registered'))
    renderWithProviders(<Register />, { auth: anonymousAuth({ register }) })

    await user.type(screen.getByLabelText(/email/i), 'taken@example.com')
    await user.type(screen.getByLabelText(/password/i), 'whatever')
    await user.click(
      screen.getByRole('button', { name: /create account|register|sign up/i }),
    )

    expect(await screen.findByText('email already registered')).toBeInTheDocument()
  })
})
