import { describe, expect, it } from 'vitest'

import { seriesYearProblem } from './series-years'

const series = (year_start, year_end) => ({
  code: 's',
  label: 'Morgan Dollar',
  extra: { year_start, year_end },
})

describe('seriesYearProblem', () => {
  it('names the series span and the year outside it', () => {
    expect(seriesYearProblem(series(1878, 1921), '1800', '1800')).toBe(
      'Morgan Dollar runs 1878-1921; 1800 is outside it. Check the year.',
    )
  })

  it.each([
    ['inside', series(1878, 1921), '1900', '1900'],
    ['the first year', series(1878, 1921), '1878', ''],
    ['the last year', series(1878, 1921), 1921, 1921],
    ['a series still struck', series(1986, null), '2024', ''],
    ['no year typed', series(1878, 1921), '', ''],
    ['a series with no years on file', series(null, null), '1800', ''],
    ['no series', undefined, '1800', ''],
  ])('says nothing for %s', (_case, s, start, end) => {
    expect(seriesYearProblem(s, start, end)).toBe('')
  })

  it('catches a range that runs past the end', () => {
    expect(seriesYearProblem(series(1878, 1921), '1920', '1925')).toBe(
      'Morgan Dollar runs 1878-1921; 1920-1925 is outside it. Check the year.',
    )
  })

  it('writes an open series as running on', () => {
    expect(seriesYearProblem(series(1986, null), '1985', '')).toBe(
      'Morgan Dollar runs 1986 on; 1985 is outside it. Check the year.',
    )
  })
})
