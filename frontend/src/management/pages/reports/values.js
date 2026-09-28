/**
 * How a report's values read: in a cell, in a parameter, and in a refusal.
 *
 * Kept apart from the components so the table, the form and the print
 * heading say a value the same way.
 */
import { date, money } from '../../../shared/format'
import { FIELD_HELP } from '../../fieldHelp'

//: Column kinds that are numbers: right-aligned, and sorted by amount.
export const NUMERIC_KINDS = new Set(['count', 'money', 'percent', 'ounces'])

/** Nothing to show: an empty cell, not "0" and not "null". */
export function isBlank(value) {
  return value === null || value === undefined || value === ''
}

/**
 * One cell as a person reads it. Money goes to `money()` as the decimal
 * string the API sent, never through a float; a date is a day; a percent
 * carries its sign. A blank is an empty string.
 */
export function cellText(kind, value) {
  if (isBlank(value)) return ''
  switch (kind) {
    case 'money':
      return money(value)
    case 'percent':
      return `${value}%`
    case 'date':
      return date(value)
    case 'count':
      return typeof value === 'number' ? value.toLocaleString('en-US') : String(value)
    default:
      return String(value)
  }
}

/**
 * Orders two rows' values for a column, blanks last whichever way it runs.
 *
 * Numbers compare by amount -- `Number()` only for the ordering; what is
 * shown is still the API's string -- and text compares as a person would,
 * so `Order-0009` comes before `Order-0012`.
 */
export function compareValues(kind, a, b, descending) {
  const blankA = isBlank(a)
  const blankB = isBlank(b)
  if (blankA || blankB) return blankA === blankB ? 0 : blankA ? 1 : -1
  const order = NUMERIC_KINDS.has(kind)
    ? Number(a) - Number(b)
    : String(a).localeCompare(String(b), 'en-US', {
        numeric: true,
        sensitivity: 'base',
      })
  return descending ? -order : order
}

/** A choice as the owner reads it: `returned_by_buyer` is "returned by buyer". */
export function choiceText(choice) {
  return String(choice).replaceAll('_', ' ')
}

/** A parameter's value in words, as the print heading gives it. */
export function paramText(param, value) {
  if (isBlank(value)) return ''
  return param.type === 'choice' ? choiceText(value) : String(value)
}

/** The help topic explaining a parameter: its own, or the general one. */
export function paramHelp(name) {
  const own = `report_${name}`
  return FIELD_HELP[own] ? own : 'report_param'
}

/**
 * `drill` with `missing=<field>` added: the items of that row missing that
 * field. Built with `URLSearchParams`, since a drill such as
 * `/inventory/currency` has no query of its own to append to.
 */
export function withMissing(drill, field) {
  const [path, query = ''] = drill.split('?')
  const params = new URLSearchParams(query)
  params.set('missing', field)
  return `${path}?${params}`
}

/**
 * Why a report did not run, in the form's own words.
 *
 * A 422 lists each refused parameter with pydantic's message; each is named
 * by the label the form shows rather than by its API name.
 */
export function refusalText(err, report) {
  const detail = err?.body?.detail
  if (err?.status !== 422 || !Array.isArray(detail)) return err?.message ?? String(err)
  return detail
    .map((problem) => {
      const name = problem.loc?.at(-1)
      const label = report.params.find((param) => param.name === name)?.label ?? name
      return label ? `${label}: ${problem.msg}` : problem.msg
    })
    .join('; ')
}
