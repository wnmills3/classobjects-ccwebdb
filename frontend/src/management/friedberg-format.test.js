import { describe, expect, it } from 'vitest'

import { frProblem, normalizeFr } from './friedberg-format'

// The same cases as backend/tests/test_fr_format.py: the two copies of the
// rule must agree. Forms only, from the 9900s -- past any real number.
describe('normalizeFr', () => {
  it.each([
    ['  9901-L ', '9901-L'],
    ['Fr. 9905-D', '9905-D'],
    ['FR#9905-d', '9905-D'],
    ['fr 12', '12'],
    ['FR-9905-D', '9905-D'],
    ['French', 'French'],
    ['9907 - l', '9907-L'],
    ['9a', '9a'],
    ['9907-lm', '9907-Lm'],
    ['9907-EM', '9907-Em'],
    ['Fr. 9907-e*m', '9907-Em*'],
    ['9908-b lgs', '9908-B LGS'],
    ['9908-B   dgs ', '9908-B DGS'],
    ['9908-b* lgs', '9908-B* LGS'],
    ['9908-em lgs', '9908-Em LGS'],
  ])('keeps %j as %j', (typed, kept) => {
    expect(normalizeFr(typed)).toBe(kept)
  })
})

// The edges of the two cleanings: space of every kind round a hyphen, runs
// of hyphens, and where a seal shade is and is not one.
describe('normalizeFr at its edges', () => {
  it.each([
    ['', ''],
    ['   ', ''],
    ['-', '-'],
    [' - ', '-'],
    ['9907\t-\tl', '9907-L'],
    ['9907  -\n l', '9907-L'],
    ['9907 - - l', '9907--L'],
    ['9907--l', '9907--L'],
    ['- 9907', '-9907'],
    ['9907 -', '9907-'],
    ['99 07', '99 07'],
    ['9908-b\tLgS', '9908-B LGS'],
    ['9908-b \n dGs', '9908-B DGS'],
    ['9908 - b  lgs', '9908-B LGS'],
    // No space before the letters: not a shade, and cleaned as a district.
    ['9908-blgs', '9908-BLGS'],
    ['lgs', 'lgs'],
    ['9908-b gs', '9908-B GS'],
    // Only the letters at the very end are a shade.
    ['9908-b lgs dgs', '9908-B LGS DGS'],
    ['9908-b lgs x', '9908-B LGS X'],
  ])('keeps %j as %j', (typed, kept) => {
    expect(normalizeFr(typed)).toBe(kept)
  })
})

describe('frProblem', () => {
  it.each([
    '1',
    '9',
    '9901',
    '9901-L',
    '9901-A*',
    '12a',
    '12a-B',
    '9907-Em',
    '9907-Em*',
    '9901m',
    '9908-B LGS',
    '9908-B DGS',
    '9908-B* LGS',
    '9908-Em* DGS',
    '9908 LGS',
  ])('passes %j', (number) => {
    expect(frProblem(number)).toBeNull()
  })

  it.each([
    ['', 'needed'],
    ['9907-', 'hyphen'],
    ['99070-L', '5 digits'],
    ['9907-M', 'form'],
    ['9907-LL', 'form'],
    ['9907-Lmm', 'form'],
    ['9907-mL', 'form'],
    ['9908-B XGS', 'form'],
    ['9908-BLGS', 'form'],
    ['9908-B LGS*', 'form'],
    ['L-9907', 'form'],
    ['99 07', 'form'],
  ])('names the slip in %j', (number, says) => {
    expect(frProblem(number)).toContain(says)
  })
})
