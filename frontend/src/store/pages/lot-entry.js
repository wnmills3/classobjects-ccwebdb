/**
 * Reading a catalogue entry that may be a LOT of coins rather than one coin.
 *
 * `CatalogItemOut` is one shape for both. A lot listing has no
 * `inventory_item` at all, so its `item_code` is null and **every**
 * item-describing field -- kind, grade, metal, year -- keeps its default,
 * because no single value of any of them describes a group. What a buyer
 * gets instead is `members`, and the shop has to read it: rendered as though
 * it were a coin, a lot came out as a bare title and a price, with no
 * picture, no specifications and no sign that it is several coins at all.
 *
 * A plain module rather than exports from a page: a component module that
 * also exports something else breaks Fast Refresh
 * (`react-refresh/only-export-components`), and the catalogue grid and the
 * detail page both need these answers to agree.
 *
 * **`members` is past tense once a lot has ended.** `_lot_entry` reads
 * `lot_writes.members_held`, not `offered_items`, precisely so a page
 * someone bookmarked still says which coins the group held after it sold --
 * `test_a_sold_lots_page_still_says_which_coins_it_held` buys one and reads
 * the page back. So nothing here may treat an empty member list as "this
 * sold": a sold lot's list is full.
 */

/**
 * Whether this entry is a lot.
 *
 * `members`, which is "empty for a listing that offers a single item ... and
 * never empty for a lot" -- the field's own contract, and the one a client
 * can read "without first asking which kind of entry it holds".
 */
export function isLot(entry) {
  return (entry.members?.length ?? 0) > 0
}

/**
 * A picture for the entry, with alt text that does not lie about it.
 *
 * A lot has no photograph of its own -- the API sends null for both
 * renditions, deliberately, because "its members carry theirs" -- so the
 * only picture available is of one coin in the group. It is shown, because a
 * card with no image at all is how the whole lot reads as broken, and the
 * alt text says which coin it is and that there are others. Inventing a
 * caption that called it a photograph of the lot would be the lie.
 */
function pictureOf(entry, key) {
  if (entry[key]) return { url: entry[key], alt: entry.title }
  if (!isLot(entry)) return null
  const member = entry.members.find((row) => row[key])
  if (member === undefined) return null
  const count = entry.members.length
  return {
    url: member[key],
    alt: `${member.title}, one of the ${count} items in this lot`,
  }
}

/** The full-size picture for a detail page, or null when there is none. */
export const coverImage = (entry) => pictureOf(entry, 'image_url')

/** The card-sized picture for the catalogue grid, or null. */
export const thumbnail = (entry) => pictureOf(entry, 'thumbnail_url')

/**
 * The one line under a title: what the thing is, in a few words.
 *
 * For a coin, its country, year and grade, as the grid has always shown. For
 * a lot, how many things are in it -- which is the fact that distinguishes a
 * group from a single coin at the price of a group, and the reason a lot
 * with none of it rendered as an anonymous, expensive-looking coin.
 *
 * Both numbers when they differ. `piece_count` is how many **objects** the
 * entry is and is summed across the members, so a lot of three entries one
 * of which is a roll of twenty is three items and twenty-two pieces. Saying
 * only one of those is how a buyer expects the wrong parcel.
 */
export function summarise(entry) {
  if (!isLot(entry)) {
    return [entry.country, entry.year_start, entry.grade_display]
      .filter(Boolean)
      .join(' - ')
  }
  const items = entry.members.length
  const head = `Lot of ${items} ${items === 1 ? 'item' : 'items'}`
  const pieces = entry.piece_count
  return pieces && pieces !== items ? `${head}, ${pieces} pieces in all` : head
}

/** One coin of a lot, in the same few words a single coin's card uses. */
export function describeMember(member) {
  return [member.country, member.year_start, member.denomination, member.grade_display]
    .filter(Boolean)
    .join(' - ')
}
