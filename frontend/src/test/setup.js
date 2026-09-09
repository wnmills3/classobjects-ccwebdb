// Adds the DOM matchers (toBeInTheDocument, toHaveValue, ...) to Vitest's
// expect, and clears the rendered tree between tests so one test's DOM cannot
// leak into the next.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)
