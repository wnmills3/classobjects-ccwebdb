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
    ['L-9907', 'form'],
    ['99 07', 'form'],
  ])('names the slip in %j', (number, says) => {
    expect(frProblem(number)).toContain(says)
  })
})
