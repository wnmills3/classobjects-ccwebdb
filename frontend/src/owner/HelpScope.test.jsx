import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import HelpScope from './HelpScope'
import { FIELD_HELP } from './fieldHelp'
import { renderWithProviders } from '../test/helpers'

function Form() {
  return (
    <HelpScope>
      <label data-help="series_year">
        Series year
        <input type="text" />
      </label>
      <label data-help="series_letter">
        Series letter
        <input type="text" />
      </label>
      <label>
        Notes
        <input type="text" />
      </label>
    </HelpScope>
  )
}

describe('HelpScope', () => {
  it('explains the field that has focus, and follows focus to the next', async () => {
    renderWithProviders(<Form />)
    expect(
      screen.getByText(/click in a field to see what it means/i),
    ).toBeInTheDocument()

    await userEvent.click(screen.getByLabelText(/series letter/i))
    // The question that started this (2026-09-23): the letter after the
    // year, not the letter in the seal.
    expect(screen.getByText(/the A in "SERIES 1963 A"/)).toBeInTheDocument()
    expect(screen.getByText(/not the letter in the seal/)).toBeInTheDocument()

    await userEvent.tab({ shift: true })
    expect(screen.getByLabelText(/series year/i)).toHaveFocus()
    expect(screen.getByText(/year the design was adopted/)).toBeInTheDocument()
    expect(screen.queryByText(/not the letter in the seal/)).toBeNull()
  })

  it('keeps the last explanation when focus moves to a field with none', async () => {
    renderWithProviders(<Form />)
    await userEvent.click(screen.getByLabelText(/series year/i))
    await userEvent.click(screen.getByLabelText(/notes/i))
    expect(screen.getByText(/year the design was adopted/)).toBeInTheDocument()
  })

  it('leaves each label naming its own field', () => {
    renderWithProviders(<Form />)
    // Nothing is added inside a label, so none is taken from its input --
    // the trap a "?" button inside the label fell into (2026-09-23).
    expect(screen.getByLabelText(/series letter/i).tagName).toBe('INPUT')
  })

  it('lets an inner scope explain its own fields in its own area', async () => {
    renderWithProviders(
      <HelpScope>
        <label data-help="denomination">
          Denomination
          <input type="text" />
        </label>
        <HelpScope>
          <label data-help="fed_district">
            District
            <input type="text" />
          </label>
        </HelpScope>
      </HelpScope>,
    )
    await userEvent.click(screen.getByLabelText(/district/i))
    expect(screen.getAllByText(/letter in the black seal/)).toHaveLength(1)
  })

  it('has help for every data-help key written in the console source', () => {
    // A misspelt key shows nothing, silently: the area just keeps whatever
    // it last said. Every literal key in the source must have its text.
    const sources = import.meta.glob(['./**/*.jsx', '!./**/*.test.jsx'], {
      query: '?raw',
      import: 'default',
      eager: true,
    })
    const keys = new Set()
    for (const text of Object.values(sources)) {
      for (const match of text.matchAll(/data-help="(\w+)"/g)) keys.add(match[1])
    }
    // Enough keys that the scan is plainly reading the forms, not nothing.
    expect(keys.size).toBeGreaterThan(30)
    const missing = [...keys].filter((key) => !FIELD_HELP[key])
    expect(missing).toEqual([])
  })

  it('has help for every banknote field the editor and lookup show', () => {
    for (const field of [
      'denomination',
      'note_type',
      'seal_color',
      'series_year',
      'series_letter',
      'signature_combination',
      'fed_district',
      'serial_number',
      'web_press',
      'fr_number',
    ]) {
      expect(FIELD_HELP[field]?.title, field).toMatch(/\w/)
      expect(FIELD_HELP[field]?.text, field).toMatch(/\w/)
    }
  })
})
