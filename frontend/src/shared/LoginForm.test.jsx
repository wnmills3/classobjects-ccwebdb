import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import LoginForm from './LoginForm'
import { renderWithProviders } from '../test/helpers'

describe('LoginForm', () => {
  it('renders the fields both applications need', () => {
    renderWithProviders(<LoginForm defaultRedirect="/" />)
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows a footer only when one is supplied', () => {
    const { unmount } = renderWithProviders(<LoginForm defaultRedirect="/" />)
    expect(screen.queryByText(/register/i)).not.toBeInTheDocument()
    unmount()
    renderWithProviders(<LoginForm defaultRedirect="/" footer={<p>Register here</p>} />)
    expect(screen.getByText(/register here/i)).toBeInTheDocument()
  })
})
