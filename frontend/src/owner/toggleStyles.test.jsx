import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

/**
 * Radios and checkboxes sit beside their labels, not on a line of their own.
 *
 * The shared stylesheet gives inputs full-width, block, bordered text-field
 * styling. Applied to a radio, that put a dark box on its own line above the
 * label on /management/receiving, and inside `.filter-grid` the column layout meant
 * for "Denomination" above a text box stacked it there too.
 *
 * jsdom resolves the cascade but does no layout, so these check the rules that
 * decide the layout -- loaded from the real stylesheets, in the order both
 * entry points import them -- rather than pixels.
 */

/**
 * The two stylesheets, in the order both entry points import them.
 *
 * Read from disk rather than imported: Vitest replaces CSS imports with empty
 * strings in tests -- `?raw` included -- which left every rule missing and
 * every assertion about a rule's absence passing for no reason at all. Located
 * from the test's own path, because under jsdom `import.meta.url` is not a
 * file URL.
 */
function stylesheets() {
  const here = dirname(expect.getState().testPath)
  return ['../shared/shared.css', './styles.css']
    .map((path) => readFileSync(resolve(here, path), 'utf8'))
    .join('\n')
}

function mount(html) {
  const style = document.createElement('style')
  style.textContent = stylesheets()
  document.head.append(style)
  document.body.innerHTML = html
}

afterEach(() => {
  document.head.innerHTML = ''
  document.body.innerHTML = ''
})

describe('toggle controls', () => {
  it('never get text-field styling', () => {
    mount(`
      <label><input type="checkbox" /> No sales tax charged</label>
      <label><input type="radio" name="mode" /> By item</label>
    `)
    for (const input of document.querySelectorAll('input')) {
      const style = getComputedStyle(input)
      expect(style.display, input.type).not.toBe('block')
      expect(style.width, input.type).not.toBe('100%')
    }
  })

  it('sit in a row before their text inside a filter grid', () => {
    mount(`
      <div class="filter-grid">
        <label class="checkbox"><input type="radio" name="mode" /> By order</label>
      </div>
    `)
    expect(getComputedStyle(document.querySelector('label')).flexDirection).not.toBe(
      'column',
    )
  })
})

describe('text fields', () => {
  it('keep their styling, so the toggle fix cannot over-reach', () => {
    mount(`<label>Denomination <input type="text" /></label>`)
    const style = getComputedStyle(document.querySelector('input'))
    expect(style.display).toBe('block')
    expect(style.width).toBe('100%')
  })

  it('still stack under their label inside a filter grid', () => {
    mount(
      `<div class="filter-grid"><label>Denomination <input type="text" /></label></div>`,
    )
    expect(getComputedStyle(document.querySelector('label')).flexDirection).toBe(
      'column',
    )
  })
})
