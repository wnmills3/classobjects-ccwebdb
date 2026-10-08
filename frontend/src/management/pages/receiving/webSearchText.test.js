import { describe, expect, it } from 'vitest'

import { webSearchText } from './webSearchText'

describe('webSearchText', () => {
  const labels = {
    denomination: { usd_note_1: '$1 Bill' },
    note_type: { frn: 'Federal Reserve Note' },
    fed_district: { B: 'B - New York' },
    signature_combination: { granahan_fowler: 'Granahan / Fowler' },
  }

  it('reads as a listing does: series, bill, type, city, signers', () => {
    expect(
      webSearchText(
        {
          seriesYear: '1963',
          seriesLetter: 'A',
          denomination: 'usd_note_1',
          noteType: 'frn',
          district: 'B',
          signatureCombination: 'granahan_fowler',
        },
        labels,
      ),
    ).toBe(
      'What is the Friedberg number for Series 1963-A $1 Federal Reserve Note New York Granahan Fowler?',
    )
  })

  it('names web press only when it is known, and no letter when there is none', () => {
    const fields = { seriesYear: '1995', denomination: 'usd_note_1', press: 'yes' }
    expect(webSearchText(fields, labels)).toBe(
      'What is the Friedberg number for Series 1995 $1 web press?',
    )
    expect(webSearchText({ ...fields, press: 'no' }, labels)).toBe(
      'What is the Friedberg number for Series 1995 $1?',
    )
  })

  it('falls back to the code while a vocabulary is still loading', () => {
    expect(webSearchText({ noteType: 'frn' })).toBe(
      'What is the Friedberg number for frn?',
    )
  })

  it('still asks a question when nothing is known yet', () => {
    expect(webSearchText({})).toBe('What is the Friedberg number for this US banknote?')
  })

  it("asks in a dealer listing's words", () => {
    expect(
      webSearchText(
        { seriesYear: '1963', seriesLetter: 'A', denomination: 'usd_note_1' },
        { denomination: { usd_note_1: '$1 Bill' } },
      ),
    ).toBe('What is the Friedberg number for Series 1963-A $1?')
  })

  it('names where it was printed, which tells two numbers apart', () => {
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
    // "m" suffix off the answer.
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
