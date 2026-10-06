import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listReferenceTables: vi.fn(),
    getReference: vi.fn(),
    addReferenceValue: vi.fn(),
    renameReferenceValue: vi.fn(),
  },
}))

import { api } from '../api'
import Vocabularies from './Vocabularies'
import { adminAuth, emptyReference, renderWithProviders } from '../../test/helpers'

function value(code, label, sort_order = 0) {
  return {
    code,
    label,
    sort_order,
    source: 'seeded',
    is_active: true,
    extra: {},
    aliases: [],
    retired_aliases: [],
  }
}

const DENOMINATION_FIELDS = [
  {
    name: 'currency',
    label: 'Currency',
    kind: 'reference',
    required: true,
    table: 'currency',
    choices: [],
  },
  {
    name: 'face_value',
    label: 'Face value',
    kind: 'decimal',
    required: true,
    choices: [],
  },
  {
    name: 'kind',
    label: 'Kind',
    kind: 'choice',
    required: true,
    choices: ['coin', 'note'],
  },
]
const GRADE_FIELDS = [
  {
    name: 'numeric_value',
    label: 'Numeric value',
    kind: 'integer',
    required: false,
    choices: [],
  },
  { name: 'is_plus', label: 'Is plus', kind: 'boolean', required: false, choices: [] },
  {
    name: 'note',
    label: 'Note',
    kind: 'text',
    required: false,
    choices: [],
    max_length: 4,
  },
]

let reference
let denominations

beforeEach(() => {
  vi.clearAllMocks()
  reference = emptyReference({
    tables: { currency: [value('USD', 'US Dollar'), value('CAD', 'Canadian Dollar')] },
  })
  denominations = [
    value('usd_coin_0_01', 'Cent', 10),
    value('usd_coin_0_05', 'Nickel', 20),
  ]
  api.listReferenceTables.mockResolvedValue([
    'series',
    'denomination',
    'grade',
    'item_status',
  ])
  api.getReference.mockImplementation(async (table) => {
    if (table === 'denomination')
      return {
        table,
        values: denominations,
        sequenced: true,
        addable: true,
        fields: DENOMINATION_FIELDS,
      }
    if (table === 'grade')
      return {
        table,
        values: [value('65', 'MS-65')],
        sequenced: true,
        fields: GRADE_FIELDS,
      }
    if (table === 'item_status')
      return {
        table,
        values: [value('ordered', 'Ordered')],
        addable: false,
        fields: [],
      }
    return {
      table,
      values: [value('morgan', 'Morgan Dollar')],
      addable: true,
      fields: [],
    }
  })
})

async function open(user, table) {
  renderWithProviders(<Vocabularies />, { auth: adminAuth(), reference })
  await screen.findByText('Morgan Dollar')
  if (table !== 'series') {
    await user.selectOptions(screen.getByLabelText('Vocabulary'), table)
    await waitFor(() => expect(api.getReference).toHaveBeenCalledWith(table))
    await waitFor(() => expect(screen.queryByText('Morgan Dollar')).toBeNull())
  }
}

const addButton = () => screen.getByRole('button', { name: 'Add value' })

describe('Adding a value to a vocabulary', () => {
  it('asks a denomination for what it is and sends it', async () => {
    const user = userEvent.setup()
    const made = { ...value('usd_coin_0_03', 'Three Cents', 15) }
    api.addReferenceValue.mockImplementation(async () => {
      denominations = [denominations[0], made, denominations[1]]
      return made
    })
    await open(user, 'denomination')
    await screen.findByText('Nickel')

    await user.click(screen.getByRole('button', { name: 'Add a value...' }))
    await user.type(screen.getByLabelText('Label'), ' Three Cents ')
    // Its own columns are required: a label alone adds nothing.
    expect(addButton()).toBeDisabled()

    await user.selectOptions(screen.getByLabelText('currency'), 'USD')
    await user.type(screen.getByLabelText('Face value'), '0.03')
    expect(addButton()).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Kind'), 'coin')
    await user.type(screen.getByLabelText('Position (optional)'), '15')
    await user.click(addButton())

    await waitFor(() =>
      expect(api.addReferenceValue).toHaveBeenCalledWith('denomination', {
        label: 'Three Cents',
        sort_order: 15,
        extra: { currency: 'USD', face_value: '0.03', kind: 'coin' },
      }),
    )
    // Listed where the server sorts it, said, and the pickers told.
    expect(
      await screen.findByText('Added Three Cents (usd_coin_0_03).'),
    ).toBeInTheDocument()
    expect(await screen.findByText('Three Cents')).toBeInTheDocument()
    expect(reference.invalidate).toHaveBeenCalledWith('denomination')
    expect(screen.queryByRole('button', { name: 'Add value' })).toBeNull()
  })

  it('needs only a label where a vocabulary has nothing of its own', async () => {
    const user = userEvent.setup()
    api.addReferenceValue.mockResolvedValue(value('peace', 'Peace Dollar'))
    await open(user, 'series')

    await user.click(screen.getByRole('button', { name: 'Add a value...' }))
    // Not in a meaningful order: no position to give.
    expect(screen.queryByLabelText('Position (optional)')).toBeNull()
    await user.type(screen.getByLabelText('Label'), 'Peace Dollar')
    await user.type(screen.getByLabelText('Code (optional)'), 'peace')
    await user.click(addButton())

    await waitFor(() =>
      expect(api.addReferenceValue).toHaveBeenCalledWith('series', {
        label: 'Peace Dollar',
        code: 'peace',
        extra: {},
      }),
    )
  })

  it('leaves out an optional column left empty, and sends a switch as it stands', async () => {
    const user = userEvent.setup()
    api.addReferenceValue.mockResolvedValue(value('64PL', 'MS-64 PL'))
    await open(user, 'grade')
    await screen.findByText('MS-65')

    await user.click(screen.getByRole('button', { name: 'Add a value...' }))
    await user.type(screen.getByLabelText('Label'), 'MS-64 PL')
    expect(screen.getByLabelText('Note (optional)')).toHaveAttribute('maxlength', '4')
    await user.click(screen.getByLabelText('Is plus (optional)'))
    await user.click(addButton())

    await waitFor(() =>
      expect(api.addReferenceValue).toHaveBeenCalledWith('grade', {
        label: 'MS-64 PL',
        extra: { is_plus: true },
      }),
    )
  })

  it('says what the server refused and keeps what was typed', async () => {
    const user = userEvent.setup()
    api.addReferenceValue.mockRejectedValue(
      new Error("series already has a value with code 'peace'"),
    )
    await open(user, 'series')

    await user.click(screen.getByRole('button', { name: 'Add a value...' }))
    await user.type(screen.getByLabelText('Label'), 'Peace')
    await user.click(addButton())

    expect(
      await screen.findByText("series already has a value with code 'peace'"),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Label')).toHaveValue('Peace')
    expect(addButton()).toBeEnabled()
  })

  it('explains the page and each box of the form', async () => {
    const user = userEvent.setup()
    await open(user, 'denomination')
    await screen.findByText('Nickel')
    // Choosing the vocabulary put focus in its picker.
    expect(screen.getByText(/Which list to work on/)).toBeInTheDocument()

    await user.click(screen.getByLabelText('Find'))
    expect(
      screen.getByText(/Narrows the list to values whose label/),
    ).toBeInTheDocument()

    // A button is explained under the pointer: clicking it would press it.
    await user.hover(screen.getByRole('button', { name: 'Rename Nickel' }))
    expect(screen.getByText(/Changes the label only/)).toBeInTheDocument()
    await user.hover(screen.getByRole('button', { name: 'Add a value...' }))
    expect(screen.getByText(/Adds a value to this list/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add a value...' }))
    await user.click(screen.getByLabelText('Face value'))
    expect(screen.getByText(/One face value is one denomination/)).toBeInTheDocument()
    await user.click(screen.getByLabelText('Kind'))
    expect(
      screen.getByText(/Whether this face value is a coin or a note/),
    ).toBeInTheDocument()
    await user.click(screen.getByLabelText('Code (optional)'))
    expect(screen.getByText(/It never changes once set/)).toBeInTheDocument()
  })

  it('explains a column with no help of its own in general terms', async () => {
    const user = userEvent.setup()
    await open(user, 'grade')
    await screen.findByText('MS-65')

    await user.click(screen.getByRole('button', { name: 'Add a value...' }))
    // `note` is a column no vocabulary really has: nothing is written for it.
    await user.click(screen.getByLabelText('Note (optional)'))
    expect(
      screen.getByText(/Something this list records about each/),
    ).toBeInTheDocument()
    await user.click(screen.getByLabelText('Numeric value (optional)'))
    expect(screen.getByText(/The grade's number on its scale/)).toBeInTheDocument()
  })

  it('changes what a vocabulary records about a value, sending only what changed', async () => {
    const user = userEvent.setup()
    denominations = [
      {
        ...value('usd_coin_0_05', 'Nickel', 20),
        extra: { currency: 'USD', face_value: '0.0500', kind: 'coin' },
      },
    ]
    api.renameReferenceValue.mockImplementation(async (table, code, payload) => ({
      ...denominations[0],
      extra: { ...denominations[0].extra, ...payload.extra },
    }))
    await open(user, 'denomination')
    await screen.findByText('Nickel')

    await user.click(screen.getByRole('button', { name: 'Edit details of Nickel' }))
    // Shown as it is held.
    expect(screen.getByLabelText('Face value')).toHaveValue('0.0500')
    expect(screen.getByLabelText('Kind')).toHaveValue('coin')
    const save = screen.getByRole('button', { name: 'Save details' })
    // Nothing changed yet: nothing to save.
    expect(save).toBeDisabled()

    await user.selectOptions(screen.getByLabelText('Kind'), 'note')
    await user.click(save)

    await waitFor(() =>
      expect(api.renameReferenceValue).toHaveBeenCalledWith(
        'denomination',
        'usd_coin_0_05',
        {
          label: 'Nickel',
          extra: { kind: 'note' },
        },
      ),
    )
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Save details' })).toBeNull(),
    )
    expect(reference.invalidate).toHaveBeenCalledWith('denomination')
  })

  it('holds a required detail and says what the server refused', async () => {
    const user = userEvent.setup()
    denominations = [
      {
        ...value('usd_coin_0_05', 'Nickel', 20),
        extra: { currency: 'USD', face_value: '0.0500', kind: 'coin' },
      },
    ]
    api.renameReferenceValue.mockRejectedValue(
      new Error(
        'denomination already has a value with the same currency, face_value, kind.',
      ),
    )
    await open(user, 'denomination')
    await screen.findByText('Nickel')
    await user.click(screen.getByRole('button', { name: 'Edit details of Nickel' }))

    await user.clear(screen.getByLabelText('Face value'))
    expect(screen.getByRole('button', { name: 'Save details' })).toBeDisabled()
    await user.type(screen.getByLabelText('Face value'), '0.10')
    await user.click(screen.getByRole('button', { name: 'Save details' }))

    expect(
      await screen.findByText(/already has a value with the same/),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Face value')).toHaveValue('0.10')
  })

  it('offers no details where a vocabulary records none', async () => {
    const user = userEvent.setup()
    await open(user, 'series')
    expect(screen.queryByRole('button', { name: /Edit details of/ })).toBeNull()
  })

  it('offers no form for a vocabulary the application acts on', async () => {
    const user = userEvent.setup()
    await open(user, 'item_status')
    await screen.findByText('Ordered')

    expect(screen.queryByRole('button', { name: 'Add a value...' })).toBeNull()
    expect(screen.getByText(/Nothing can be added to item_status/)).toBeInTheDocument()
  })

  it('closes the form when another vocabulary is chosen', async () => {
    const user = userEvent.setup()
    await open(user, 'series')
    await user.click(screen.getByRole('button', { name: 'Add a value...' }))
    await user.type(screen.getByLabelText('Label'), 'Half typed')

    await user.selectOptions(screen.getByLabelText('Vocabulary'), 'grade')
    await screen.findByText('MS-65')

    expect(screen.queryByLabelText('Label')).toBeNull()
    expect(screen.getByRole('button', { name: 'Add a value...' })).toBeInTheDocument()
  })
})
