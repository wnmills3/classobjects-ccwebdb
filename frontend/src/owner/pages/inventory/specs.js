/**
 * What each inventory view shows and filters on.
 *
 * Separated from the components because three of them read it -- the table,
 * the filter panel and the review pane -- and a specification imported in
 * three places should not live inside any one of them.
 *
 * `detail` is shown on a second line under each item, across the columns.
 * The description is long and read rather than compared, so it no longer
 * takes a column; that leaves room for the denomination beside the code.
 *
 * `searchExamples` are the "Search tips" under the search box, and each one
 * runs when clicked. The box has no field syntax: one term, matched anywhere
 * in the title, description, rating or item code, ignoring case -- plus any
 * series whose name or nickname contains it. Every example here was run
 * against the collection on 2026-09-16 and chosen for what it teaches;
 * the counts in the comments are from that run. The one most worth showing is
 * that several words are a single phrase in order: "1921 morgan" found 27
 * items and "morgan 1921" found none.
 */

/**
 * Accelerators every view shares. Each view's own filters carry their letter
 * as the last element of their spec entry, chosen to avoid these; the test
 * checks each view as a whole for repeats and for D, E and F, which the
 * browsers keep for the address bar and menus. Year from and Year to match
 * the item editor's Y and O, so a key means the same thing in both places.
 */
export const SHARED_KEYS = {
  search: 's',
  tips: 'h',
  yearFrom: 'y',
  yearTo: 'o',
  clear: 'c',
}

export const COIN_VIEW = {
  view: 'coins',
  title: 'Coins & bullion',
  columns: [
    ['Code', 'item_code', 'mono'],
    ['Denomination', 'denomination_label'],
    ['Year', 'year_start'],
    ['Mint', 'mint_mark'],
    ['Series', 'series_label'],
    ['Kind', 'item_kind'],
    ['Grade', 'grade'],
    ['Metal', 'metal'],
    ['Fine ozt', 'fine_weight_ozt'],
    ['Qty', 'piece_count'],
    ['Cost', 'total_cost', 'money'],
    ['Order', 'order_number', 'order'],
    ['Status', 'status'],
  ],
  detail: 'description',
  searchPlaceholder:
    'Search title, description, rating, item code or series name, e.g. mercury (tips below)',
  searchExamples: [
    // 112 -- mostly through the series nickname, not the listing text.
    [
      'mercury',
      'a series name or nickname: finds Winged Liberty Head dimes even where the listing never says "Mercury"',
    ],
    // 27, against 0 for "morgan 1921".
    [
      '1921 morgan',
      'several words are one phrase, in that order: "morgan 1921" finds nothing',
    ],
    // 8
    ['morgan%1921', '% matches anything in between, so the words can be apart'],
    // 278
    ['19_5', '_ matches exactly one character: 1905, 1915 ... 1995'],
    // 128, against 16 for "ms-65".
    ['ms65', 'text is matched as written: "ms65" and "ms-65" find different items'],
    // 100
    ['cc-0012', 'part of an item code'],
  ],
  // The last element of each filter is its Alt+letter accelerator. FilterPanel
  // uses SHARED_KEYS, above, for the ones common to both views (search, tips,
  // clear, years); FilterPanel.test.jsx checks every view for repeats, for D/E/F,
  // and that each letter appears in its label so it can be underlined.
  textFilters: [['Item code', 'item_code', 'CC-000123', 'i']],
  facetFilters: [
    ['Denomination', 'denomination', 'denomination', 'm'],
    ['Series', 'series', 'series', 'r'],
    ['Kind', 'kind', 'item_kind', 'k'],
    ['Metal', 'metal', 'metal', 'l'],
    ['Grade', 'grade', 'grade', 'g'],
    ['Mint', 'mint', 'mint_mark', 't'],
    ['Country', 'country', 'country', 'u'],
    // A, not U as on the currency view: Country can only take U here.
    ['Status', 'status', 'status', 'a'],
    ['Disposition', 'disposition', 'disposition', 'n'],
  ],
  // Named diagnostics. The code is the contract, appears in bookmarked URLs,
  // and is what the chip's label is derived from; the explanation on its
  // tooltip comes from the API's `issue_descriptions`, so a check's reasoning
  // -- why bullion is excluded, what "malformed" means here -- is written
  // once on the server rather than copied into this file and left to drift.
  issueChecks: [
    'no_year',
    'no_country',
    'no_grade',
    'no_denomination',
    'no_weight_bullion',
    'mixed_marker',
    'zero_cost',
    'kind_unknown',
    'repeated_identity',
    'unreviewed',
  ],
}

export const CURRENCY_VIEW = {
  view: 'currency',
  title: 'Currency',
  columns: [
    ['Code', 'item_code', 'mono'],
    ['Denomination', 'denomination_label'],
    ['Series name', 'series_label'],
    ['Series', 'series_designation'],
    ['Seal', 'seal_color'],
    ['District', 'fed_district_letter'],
    ['Serial', 'serial_number', 'mono'],
    ['Fr#', 'friedberg_number'],
    // The label, because a note grade's code (`N64`) is not what anyone reads.
    ['Grade', 'grade_label'],
    ['Cost', 'total_cost', 'money'],
    ['Order', 'order_number', 'order'],
    ['Status', 'status'],
  ],
  detail: 'description',
  // Series names match here too, once app.series_classify has assigned the
  // note designs (Funnyback, Barr Note, Hawaii, North Africa). A note's
  // series year is its own dropdown.
  searchPlaceholder:
    'Search title, description, rating or item code, e.g. funny%back (tips below)',
  searchExamples: [
    // 48. The word lives only in the Rating text, spelled "Funnyback" (18)
    // and "Funny Back" (30); % is what finds both.
    [
      'funny%back',
      'the rating is searched too, and % bridges the two spellings the sheet uses',
    ],
    // 53
    ['silver certificate', 'several words are one phrase, in that order'],
    // 3, against 1 for "1957 silver".
    ['1957%silver', '% matches anything in between, so the words can be apart'],
    // 129
    ['fancy', 'listings that call out a fancy serial'],
    // 125
    ['error', 'listings that describe an error note'],
    // 197
    [
      'star',
      'listings that mention a star; the Serial number box searches the note itself',
    ],
    // 87
    ['cc-0070', 'part of an item code'],
  ],
  textFilters: [
    ['Serial number', 'serial_number', 'B0808450_  (_ = one char, % = any)', 'b'],
    ['Item code', 'item_code', 'CC-000123', 'i'],
  ],
  facetFilters: [
    ['Denomination', 'denomination', 'denomination', 'm'],
    ['Series', 'series', 'series', 'r'],
    ['Note type', 'note_type', 'note_type', 'p'],
    ['Seal', 'seal_color', 'seal_color', 'l'],
    ['District', 'fed_district', 'fed_district_letter', 't'],
    ['Grade', 'grade', 'grade', 'g'],
    // A is the only free letter in "Series year", so Status takes U here.
    ['Series year', 'series_year', 'series_year', 'a'],
    ['Status', 'status', 'status', 'u'],
    ['Disposition', 'disposition', 'disposition', 'n'],
  ],
  // Named diagnostics. The code is the contract, appears in bookmarked URLs,
  // and is what the chip's label is derived from; the explanation on its
  // tooltip comes from the API's `issue_descriptions`, so a check's reasoning
  // -- why bullion is excluded, what "malformed" means here -- is written
  // once on the server rather than copied into this file and left to drift.
  // kind_unknown is not offered here: CURRENCY_VIEW's own filter requires
  // k.code = 'currency', so an item of kind 'unknown' can never appear in
  // this view for the check to find. COIN_VIEW is everything that is not
  // currency, which is where that check actually lives.
  issueChecks: [
    'no_year',
    'no_country',
    'no_grade',
    'no_denomination',
    'mixed_marker',
    'zero_cost',
    'repeated_identity',
    'unreviewed',
    'star_mismatch',
    'malformed_serial',
    'near_duplicate_serial',
  ],
}

export const PAGE_SIZE = 50
