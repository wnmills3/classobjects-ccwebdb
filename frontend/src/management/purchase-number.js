/**
 * A purchase's own number: the id the database gave it, written `#3974`.
 *
 * Unlike the order number, which is the vendor's and may be missing or shared
 * across vendors, every purchase has one and no two share it. The reports and
 * Receiving's links name a purchase by it, so the console shows it in one
 * form everywhere and the Purchases filter finds a purchase by it.
 */

/** `#3974`: how a purchase's own number is shown. */
export function purchaseNumber(id) {
  return `#${id}`
}

//: What a person types for a purchase number: digits, with or without the `#`.
const TYPED = /^#?(\d+)$/

/**
 * Whether `text` names exactly the purchase numbered `id` -- `3974` or
 * `#3974`, never part of a longer number, so `#11` does not find #112.
 */
export function namesPurchase(text, id) {
  const match = TYPED.exec(text.trim())
  return match !== null && Number(match[1]) === id
}
