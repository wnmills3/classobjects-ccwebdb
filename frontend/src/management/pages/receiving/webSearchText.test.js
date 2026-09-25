import { describe, expect, it } from 'vitest'

import { webSearchText } from './webSearchText'

describe('webSearchText', () => {
  it("asks in a dealer listing's words", () => {
    expect(
      webSearchText(
        { seriesYear: '1963', seriesLetter: 'A', denomination: 'usd_note_1' },
        { denomination: { usd_note_1: '$1 Bill' } },
      ),
    ).toBe('What is the Friedberg number for Series 1963-A $1?')
  })

  it('names where it was printed, which tells 3005-A from 3006-A', () => {
    const text = webSearchText({
      seriesYear: '2017',
      seriesLetter: 'A',
      printing: 'fw',
    })
    expect(text).toBe(
      'What is the Friedberg number for Series 2017-A printed in Fort Worth?',
    )
  })

  it('gives the plates and asks for the m suffix of a mule', () => {
    // A face and back from different eras make a mule; the owner reads the
    // "m" suffix off the answer (2026-09-25).
    const text = webSearchText({
      seriesYear: '1935',
      facePlate: 'E82',
      backPlate: '1234',
      printing: 'dc',
    })
    expect(text).toBe(
      'What is the Friedberg number for Series 1935 printed in Washington DC ' +
        'with face plate E82 and back plate 1234? ' +
        'If it is a mule, give the number with its m suffix.',
    )
  })
})
