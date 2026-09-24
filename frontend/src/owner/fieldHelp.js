/**
 * What a form field means, in the words the owner reads off the item.
 *
 * One place for every form's help text, keyed by the field's API name, so
 * the item editor, Receiving and the Friedberg lookup explain a field the
 * same way. `HelpScope` shows the entry for whichever field has focus; a
 * field opts in with `data-help="<key>"` on its label.
 *
 * Public numismatic fact only -- what is printed where on a note, and what
 * it means. Never a catalogue's numbering or a price guide's values, per
 * CLAUDE.md's reference-data rule.
 */
export const FIELD_HELP = {
  // -- the purchase ---------------------------------------------------------
  vendor: {
    title: 'Vendor',
    text:
      'Who sold it: the website, auction house, dealer or person. Add a new one ' +
      "if it isn't listed.",
  },
  ordered_on: {
    title: 'Order date',
    text:
      'The date of the purchase -- when the order was placed or the auction won -- ' +
      'not the date it arrived. Arrival is recorded in Receiving.',
  },
  source_url: {
    title: 'Web address',
    text:
      'A link to the listing, auction lot or invoice, so the purchase can be ' +
      'checked later. It appears as "Vendor page" on the order.',
  },
  purchase_notes: {
    title: 'Notes',
    text: 'Anything about the purchase as a whole, rather than one item on it.',
  },
  purchase_filter: {
    title: 'Filter',
    text: 'Type part of an order number or a vendor name to shorten the list.',
  },
  tax_rate: {
    title: 'Tax rate',
    text:
      'The sales tax rate charged on this purchase, as a decimal: 0.0635 for 6.35%. ' +
      'Leave it blank to use the configured default. Every item entered below ' +
      'is stamped with it.',
  },
  no_sales_tax: {
    title: 'No sales tax charged',
    text:
      'Tick when the seller charged no sales tax at all -- the items are stamped ' +
      'with a rate of zero rather than the default.',
  },
  tax_includes_shipping: {
    title: 'Tax on shipping',
    text:
      'Whether the tax was charged on shipping as well as on the price. States ' +
      'differ; "As configured" uses the default setting.',
  },
  // -- an item --------------------------------------------------------------
  item_kind: {
    title: 'Kind',
    text:
      'What the item is: a coin, a banknote, bullion, a set, and so on. It decides ' +
      'which fields apply -- a note has no metal or strike type, a coin no serial ' +
      'number.',
  },
  source_title: {
    title: 'Title',
    text:
      'What the seller called it, as written in the listing or invoice -- kept as ' +
      "the seller's words. The title a buyer sees is set when it is offered for sale.",
  },
  piece_count: {
    title: 'Pieces',
    text:
      'How many pieces this one entry stands for: 1 for a single coin or note, more ' +
      'for a roll, a set or a lot bought as one. A lot can be split into its ' +
      'pieces later.',
  },
  item_cost: {
    title: 'Item cost',
    text:
      'What was paid for this item, in dollars, before shipping and tax. On an ' +
      'order of several items, the price of this one.',
  },
  shipping_cost: {
    title: 'Shipping',
    text: "This item's share of the shipping charged on the order, in dollars.",
  },
  country: {
    title: 'Country',
    text: 'The country that issued it.',
  },
  errors: {
    title: 'Errors',
    text:
      'Printing or minting errors recorded on the item, each with a note of ' +
      "where and how -- an offset's direction, a miscut's size.",
  },
  cert_numbers: {
    title: 'Certificate no.',
    text:
      "The certification number printed on the holder's label, used to look it up " +
      "on the grader's site. Separate several with commas. A number added here is " +
      'recorded as graded by the grading service above.',
  },
  strike_type: {
    title: 'Strike type',
    text:
      'How the coin was made: a business strike, made for circulation and graded ' +
      'Mint State (MS), or a proof, struck with specially polished dies for ' +
      'collectors and graded PR. It decides whether a 65 reads MS65 or PR65.',
  },
  grade: {
    title: 'Grade',
    text:
      'The condition, on the 1 to 70 scale graders use -- 70 is perfect. Coins and ' +
      'notes each have their own scale in this list.',
  },
  grade_designation: {
    title: 'Grade designation',
    text:
      'A qualifier the grader adds to the grade, such as DCAM (Deep Cameo) on a ' +
      'proof, RD (Red) on copper, or EPQ (Exceptional Paper Quality) on a note. ' +
      "Only the ones that fit the item's kind are offered.",
  },
  grading_service: {
    title: 'Grading service',
    text:
      'The company that graded and sealed it in a holder ("slab") -- PCGS, NGC, ' +
      'PMG and others. Leave blank for an ungraded ("raw") item.',
  },
  cert_number: {
    title: 'Certificate number',
    text:
      "The grading company's certification number, printed on the slab's label; " +
      "it identifies this one item on the company's website. Check it against the " +
      "label: numbers copied from a seller's listing are sometimes wrong.",
  },
  metal: {
    title: 'Metal',
    text:
      'What it is struck in: silver, gold, copper, copper-nickel clad, and so on. ' +
      'With the fineness and weight, it decides the melt value.',
  },
  series: {
    title: 'Series',
    text:
      'The design it belongs to, as collectors name it: Morgan Dollar, Walking ' +
      'Liberty Half Dollar, Silver Certificate.',
  },
  variety: {
    title: 'Variety',
    text:
      'A die variety or other variant within the date and mint -- a doubled die, ' +
      'an overdate -- as the seller or grader names it.',
  },
  year_start: {
    title: 'Year',
    text:
      'The date on the coin. For a set, or a coin dated only to an era, tick ' +
      '"Range of years" and give the first and last year.',
  },
  year_end: {
    title: 'Year to',
    text: 'The last year of the range.',
  },
  new_item_status: {
    title: 'Status',
    text:
      'Ordered: bought but not yet in hand -- record its arrival later in ' +
      'Receiving. Received: already in hand.',
  },
  description: {
    title: 'Description',
    text:
      "Anything else worth keeping about this item: the seller's full description, " +
      'notes on condition or where it came from.',
  },
  purchase_mode: {
    title: 'Existing or new purchase',
    text:
      'Add to an existing purchase when more items from an order already entered ' +
      'need recording; start a new one for a new order. Every item belongs to ' +
      'exactly one purchase.',
  },
  // -- searching and receiving ----------------------------------------------
  search_kind: {
    title: 'Search for',
    text:
      'Any: coins and notes together -- one parcel can hold both. Coins: adds a ' +
      'search by year and mint. Currency: adds a search by serial number and ' +
      'series year.',
  },
  order_number: {
    title: 'Order number',
    text:
      "The seller's order or invoice number, from the receipt, email or packing " +
      'slip. When searching, part of it is enough: "4452" finds order ' +
      '114-4452-X, upper or lower case alike.',
  },
  status: {
    title: 'Status',
    text:
      'Where the item is: ordered (bought, not yet arrived), received, missing ' +
      '(written off as lost in the post), and so on. "Not yet arrived" means ' +
      'ordered or missing -- a missing parcel sometimes turns up. "Any status" ' +
      'shows everything, which is how to see a whole order including what ' +
      'already came.',
  },
  year: {
    title: 'Year',
    text: 'The date struck on the coin.',
  },
  mint: {
    title: 'Mint',
    text:
      'The mint mark on the coin: P Philadelphia, D Denver, S San Francisco, ' +
      'O New Orleans, CC Carson City, W West Point. Most older Philadelphia ' +
      'coins carry no mark.',
  },
  denomination: {
    title: 'Denomination',
    text: 'The face value printed on the note or coin -- $1, $5, a half dollar.',
  },
  note_type: {
    title: 'Note type',
    text:
      'The kind of note, printed across the top of the face: Federal Reserve Note, ' +
      'Silver Certificate, United States Note, and so on.',
  },
  seal_color: {
    title: 'Seal colour',
    text:
      'The colour of the Treasury seal, right of centre on the face: green on a ' +
      'Federal Reserve Note, blue on a Silver Certificate, red on a United States ' +
      'Note; brown, gold and yellow on some older and wartime issues.',
  },
  series_year: {
    title: 'Series year',
    text:
      'The year after the word SERIES on the face, near the signatures -- "SERIES ' +
      '1963 A" is 1963. It is the year the design was adopted, not the year the ' +
      'note was printed: notes of one series are often printed for years afterwards.',
  },
  series_letter: {
    title: 'Series letter',
    text:
      'The letter after the series year -- the A in "SERIES 1963 A". It marks a ' +
      'later printing of the same series, usually because a new Treasurer or ' +
      'Secretary signed it. Leave it blank when the series has no letter, as SERIES ' +
      '1995 does. It is not the letter in the seal: that is the Reserve Bank.',
  },
  signature_combination: {
    title: 'Signatures',
    text:
      'The two signatures printed on the face: the Treasurer of the United States on ' +
      'the left, the Secretary of the Treasury on the right. Each series letter ' +
      'usually has its own pair.',
  },
  fed_district: {
    title: 'Reserve Bank (district)',
    text:
      'The Federal Reserve Bank that issued the note: the letter in the black seal ' +
      'on the left of the face (A Boston to L San Francisco; B is New York). The ' +
      'same bank shows as the number printed four times around the face (2 for ' +
      'New York).',
  },
  serial_number: {
    title: 'Serial number',
    text:
      'The number printed twice on the face, with a letter before and after. On ' +
      'most Federal Reserve Notes the first letter is the Reserve Bank (B for New ' +
      'York); on $5 and higher from Series 1996 the first letter marks the series ' +
      'and the second the bank. A star in place of the last letter marks a ' +
      'replacement note.',
  },
  web_press: {
    title: 'Web press',
    text:
      "Printed on the Bureau of Engraving and Printing's experimental web press, " +
      'from a roll of paper, instead of the usual sheet-fed press. Only some $1 ' +
      'notes of series 1988-A, 1993 and 1995 were. The two printings have ' +
      'different Friedberg numbers. Leave it as not known unless you can tell.',
  },
  fr_number: {
    title: 'Friedberg number',
    text:
      'The Friedberg catalogue number for this type of note, such as 1901-B: a ' +
      'number for the type and, on a Federal Reserve Note, the Reserve Bank ' +
      'letter. Read it off the slab or holder or a reference, or copy it from a ' +
      'match in your own catalogue.',
  },
}
