import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ErrorBoundary from './ErrorBoundary'

/** A component whose every render fails. */
function Broken() {
  throw new Error('a malformed line')
}

afterEach(() => vi.restoreAllMocks())

describe('ErrorBoundary', () => {
  it('shows what it wraps while nothing fails', () => {
    render(
      <ErrorBoundary>
        <p>the shop</p>
      </ErrorBoundary>,
    )
    expect(screen.getByText('the shop')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('tells the shopper to reload instead of leaving a blank page', () => {
    // React reports the caught error on the console too; keep the run quiet.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByRole('link', { name: 'reload the page' })).toBeInTheDocument()
  })
})
