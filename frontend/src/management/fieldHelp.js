/**
 * What a form field means, in the words the owner reads off the item.
 *
 * One place for every form's help text, keyed by the field's API name, so
 * the item editor, Receiving and the Friedberg lookup explain a field the
 * same way. `HelpScope` shows the entry for whichever field has focus; a
 * field opts in with `data-help="<key>"` on its label.
 *
 * Public numismatic fact only -- what is printed where on a note, and what
 * it means. Never a catalog's numbering or a price guide's values, per
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
  sellers_item_id: {
    title: "Seller's item id",
    text:
      "The seller's own number for the listing this was bought from -- eBay's " +
      'item number. Every piece split from one listing keeps it, so a lot stays ' +
      'traceable. Opens the listing on eBay in a new tab.',
  },
  item_purchase: {
    title: 'Purchase',
    text:
      'The purchase this item was bought on: its order number and vendor. Opens the ' +
      'purchase in a new tab, where Edit details changes its number, date or notes.',
  },
  edit_purchase: {
    title: 'Edit details',
    text:
      "Change this purchase's order number, date, web address or notes. Clear the " +
      'order number to give it the next generated one (Order-0001, Order-0002, ...).',
  },
  suggest_description: {
    title: 'Suggest description',
    text:
      'Writes a description from what is entered, in your usual order: the grade ' +
      'and what makes the piece special first (fancy serial, errors), then what it ' +
      'is. It replaces the Description box; edit it before saving.',
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
    title: 'Seal color',
    text:
      'The color of the Treasury seal, right of center on the face: green on a ' +
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
  face_plate_number: {
    title: 'Face plate',
    text:
      'The small plate number on the front, beside the check letter: a letter and ' +
      'digits (E82), or digits alone on older notes (153). A note printed in Fort ' +
      'Worth has FW before it (FW E82) -- that is what sets Printed at. With the ' +
      'back plate, it is how a mule is found.',
  },
  back_plate_number: {
    title: 'Back plate',
    text:
      'The plate number printed on the back, digits only (1234). A face and back ' +
      'plate from different eras make a mule; the Friedberg web search asks about ' +
      'both and answers with an m suffix when it is one.',
  },
  printing_facility: {
    title: 'Printed at',
    text:
      'Where the note was printed: Washington, DC or Fort Worth, TX. Read from the ' +
      'face plate when there is one (FW before it is Fort Worth); choose it here ' +
      'only for a note whose face plate is not recorded. It tells some Friedberg ' +
      'numbers apart -- a 2017-A $1 is 3005-A from Washington, 3006-A from Fort Worth.',
  },
  series_letter: {
    title: 'Series letter',
    text:
      'The letter after the series year -- the A in "SERIES 1963 A". It marks a ' +
      'later printing of the same series, usually because a new Treasurer or ' +
      'Secretary signed it. Leave it blank when the series has no letter, as SERIES ' +
      '1995 does. It is not the letter in the seal: that is the Reserve Bank. ' +
      'If no note of that value was issued in that series, the form says so and ' +
      'lists the series that were.',
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
      'The Friedberg catalog number for this type of note, such as 1901-B: a ' +
      'number for the type and, on a Federal Reserve Note, the Reserve Bank ' +
      'letter. Read it off the slab or holder or a reference, or copy it from a ' +
      'match in your own catalog.',
  },
  // -- more of an item --------------------------------------------------------
  disposition: {
    title: 'Disposition',
    text:
      'What has become of the item once it is yours: held, listed, sold, shipped, ' +
      'delivered, returned. Status says whether it has arrived; disposition says ' +
      'where it stands in selling.',
  },
  reviewed: {
    title: 'Confirmed',
    text:
      'Tick once you have checked this field against the item itself, not the ' +
      "seller's listing. It records that a person looked; the rating pass leaves " +
      'a confirmed field alone.',
  },
  year_range: {
    title: 'Range of years',
    text:
      'Tick when the item covers several years -- a set, a roll, a lot of mixed ' +
      'dates -- to give a first and last year instead of one.',
  },
  attributes: {
    title: 'Attributes',
    text:
      'What makes this piece special beyond its grade: Star Note, Radar, First ' +
      'Strike, CAC and the like. Type in Find to narrow the list, choose one to add ' +
      "it, and use its x to remove it. Only those that fit the item's kind are " +
      'offered.',
  },
  error_details: {
    title: 'Error details',
    text:
      'A note on this error: where and how -- "Back to Front", "left margin", ' +
      '"3 o\'clock, 4mm". Clearing it removes only the note; to remove the error, ' +
      'use its Remove button.',
  },
  photos: {
    title: 'Photographs',
    text:
      "The item's pictures. Choose a file to add one, set each one's role " +
      '(obverse, reverse, slab ...), make one the primary -- the one the shop shows ' +
      "first -- or remove one. Photographs are filed straight away; the form's Save " +
      'is not needed.',
  },
  // -- the search panel -------------------------------------------------------
  search_text: {
    title: 'Search',
    text:
      'Finds items whose title, description, rating or item code contain what you ' +
      'type, ignoring case. Several words are one phrase, in that order. % stands ' +
      'for any run of characters and _ for exactly one. Press Enter or leave the ' +
      'box to search.',
  },
  search_tips: {
    title: 'Search tips',
    text: 'Worked examples of the search box; click one to run it.',
  },
  grade_filter: {
    title: 'Grade',
    text:
      'A grade or a pattern: 65, 64+, PR69, UNC, or 55% for 55 and every grade ' +
      'written starting with 55. Coins and notes each use their own scale.',
  },
  serial_filter: {
    title: 'Serial number',
    text:
      'All or part of a serial, anywhere in it. _ stands for exactly one ' +
      'character, so B0808450_ finds a run of consecutive notes; % for any run.',
  },
  item_code_filter: {
    title: 'Item code',
    text: 'All or part of an item code: CC-006140, or 6140.',
  },
  year_from: {
    title: 'Year from',
    text: 'Only items dated this year or later. Leave blank for no lower limit.',
  },
  year_to: {
    title: 'Year to',
    text: 'Only items dated this year or earlier. Leave blank for no upper limit.',
  },
  issue_checks: {
    title: 'Checks',
    text:
      'Ready-made lists of items needing attention, with how many each finds: no ' +
      'grade, not yet reviewed, and so on. Click one to show only those items; ' +
      'click it again to go back. Only checks with something to find are shown.',
  },
  clear_filters: {
    title: 'Clear filters',
    text: 'Empties the search box and every filter, showing everything in this view.',
  },
  select_page: {
    title: 'Select all shown',
    text:
      'Ticks every item on this page -- this page only, not every match -- to act ' +
      'on them together: review them, edit them in bulk, or offer them for sale.',
  },
  select_row: {
    title: 'Select',
    text:
      'Tick items to act on them together: review them one after another, edit ' +
      'them in bulk, or offer them for sale.',
  },
}
