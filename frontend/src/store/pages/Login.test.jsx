import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import Login from './Login'
import { renderWithProviders } from '../../test/helpers'

// Signing in itself is the shared form's, and tested with it
// (shared/LoginForm.test.jsx). What is the shop's own is the page around it.
describe('Login', () => {
  it('renders the sign-in form', () => {
    renderWithProviders(<Login />)
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('offers registration to someone without an account', () => {
    renderWithProviders(<Login />)
    expect(
      screen.getByRole('link', { name: /register as a customer/i }),
    ).toHaveAttribute('href', '/register')
  })
})
