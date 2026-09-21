import { describe, expect, it } from 'vitest'

import { coverImage, describeMember, isLot, summarise, thumbnail } from './lot-entry'

const COIN = {
  title: 'Morgan Dollar 1921',
  country: 'US',
  year_start: 1921,
  grade_display: 'MS64',
  thumbnail_url: '/t/coin.jpg',
  image_url: '/w/coin.jpg',
  piece_count: 1,
  members: [],
}

const LOT = {
  title: 'Three Morgan Dollars',
  country: null,
  year_start: null,
  grade_display: null,
  thumbnail_url: null,
  // Two single-piece coins: `piece_count` is the SUM of the members' own
  // counts, so it agrees with the member count until one of them is a roll
  // or a set.
  image_url: null,
  piece_count: 2,
  members: [
    { inventory_item_id: 7, title: 'First', thumbnail_url: null, image_url: null },
    {
      inventory_item_id: 9,
      title: 'Second',
      thumbnail_url: '/t/two.jpg',
      image_url: '/w/two.jpg',
    },
  ],
}

describe('isLot', () => {
  it('is true of an entry with members', () => {
    expect(isLot(LOT)).toBe(true)
  })

  it('is false of a single coin', () => {
    // `members` is "empty for a listing that offers a single item", which is
    // the contract this reads.
    expect(isLot(COIN)).toBe(false)
  })

  it('is false when the field is missing entirely', () => {
    expect(isLot({ title: 'Old response' })).toBe(false)
  })
})

describe('summarise', () => {
  it('describes a coin by its own attributes', () => {
    expect(summarise(COIN)).toBe('US - 1921 - MS64')
  })

  it('counts the members of a lot', () => {
    expect(summarise(LOT)).toBe('Lot of 2 items')
  })

  it('counts the pieces too when they differ from the members', () => {
    // Three entries, one of which is a roll of twenty. Both numbers are
    // true, and `piece_count` is the one that says what arrives in the box.
    expect(summarise({ ...LOT, piece_count: 22 })).toBe(
      'Lot of 2 items, 22 pieces in all',
    )
  })

  it('invents no count when the entry carries none', () => {
    // A response with no `piece_count` is named, not counted. "undefined
    // pieces in all" would be worse than saying less.
    expect(summarise({ ...LOT, piece_count: undefined })).toBe('Lot of 2 items')
  })

  it('says item, singular, for a lot of one', () => {
    expect(summarise({ ...LOT, members: [LOT.members[0]], piece_count: 1 })).toBe(
      'Lot of 1 item',
    )
  })
})

describe('pictures', () => {
  it("uses a coin's own photograph", () => {
    expect(coverImage(COIN)).toEqual({
      url: '/w/coin.jpg',
      alt: 'Morgan Dollar 1921',
    })
    expect(thumbnail(COIN).url).toBe('/t/coin.jpg')
  })

  it('falls through to the first photographed member of a lot', () => {
    // The FIRST that has one, not the first member: the lot above leads with
    // an unphotographed coin, and taking `members[0]` would hand the page a
    // null src and an image that never loads.
    expect(coverImage(LOT)).toEqual({
      url: '/w/two.jpg',
      alt: 'Second, one of the 2 items in this lot',
    })
    expect(thumbnail(LOT).url).toBe('/t/two.jpg')
  })

  it('is null when nothing in the lot has been photographed', () => {
    // Most of a real collection is unphotographed. The card then draws its
    // deliberate empty frame rather than a broken image.
    const unphotographed = {
      ...LOT,
      members: LOT.members.map((m) => ({ ...m, thumbnail_url: null, image_url: null })),
    }
    expect(coverImage(unphotographed)).toBeNull()
    expect(thumbnail(unphotographed)).toBeNull()
  })

  it('does not go looking for a member when a single coin has no photograph', () => {
    expect(coverImage({ ...COIN, image_url: null })).toBeNull()
  })
})

describe('describeMember', () => {
  it('names a coin in the words a card uses', () => {
    expect(
      describeMember({
        country: 'US',
        year_start: 1881,
        denomination: 'dollar',
        grade_display: 'MS64',
      }),
    ).toBe('US - 1881 - dollar - MS64')
  })

  it('leaves out what is not known rather than printing a gap', () => {
    expect(describeMember({ country: 'US', year_start: null })).toBe('US')
  })
})
