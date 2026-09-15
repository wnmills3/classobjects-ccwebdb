// Adds the DOM matchers (toBeInTheDocument, toHaveValue, ...) to Vitest's
// expect, and clears the rendered tree between tests so one test's DOM cannot
// leak into the next.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)

// jsdom has the <dialog> element but not its modal methods, so a component
// calling showModal() throws before anything can be asserted. These do what
// the browser does to the DOM -- toggle `open`, fire `close` -- and nothing
// more: no top layer, no inert background, no focus move.
if (!HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute('open', '')
  }
  HTMLDialogElement.prototype.close = function close() {
    if (!this.hasAttribute('open')) return
    this.removeAttribute('open')
    this.dispatchEvent(new Event('close'))
  }
}
