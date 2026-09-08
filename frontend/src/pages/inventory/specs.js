/**
 * What each inventory view shows and filters on.
 *
 * Separated from the components because three of them read it -- the table,
 * the filter panel and the review pane -- and a specification imported in
 * three places should not live inside any one of them.
 */

export const COIN_VIEW = {
  view: 'coins',
  title: 'Coins & bullion',
  columns: [
    ['Code', 'item_code', 'mono'],
    ['Description', 'description', 'wide'],
    ['Series', 'series_label'],
    ['Kind', 'item_kind'],
    ['Year', 'year_start'],
    ['Mint', 'mint_mark'],
    ['Grade', 'grade'],
    ['Metal', 'metal'],
    ['Fine ozt', 'fine_weight_ozt'],
    ['Qty', 'piece_count'],
    ['Cost', 'total_cost', 'money'],
    ['Status', 'status'],
  ],
  textFilters: [['Item code', 'item_code', 'CC-000123']],
  facetFilters: [
    ['Series', 'series', 'series'],
    ['Kind', 'kind', 'item_kind'],
    ['Metal', 'metal', 'metal'],
    ['Grade', 'grade', 'grade'],
    ['Mint', 'mint', 'mint_mark'],
    ['Country', 'country', 'country'],
    ['Status', 'status', 'status'],
    ['Disposition', 'disposition', 'disposition'],
  ],
  // Named diagnostics. The code is the contract and appears in bookmarked
  // URLs; the wording comes from the API's `description`, so it is written
  // once on the server rather than twice.
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
    ['Description', 'description', 'wide'],
    ['Series name', 'series_label'],
    ['Denomination', 'denomination_label'],
    ['Series', 'series_designation'],
    ['Seal', 'seal_color'],
    ['District', 'fed_district_letter'],
    ['Serial', 'serial_number', 'mono'],
    ['Fr#', 'friedberg_number'],
    ['Grade', 'grade'],
    ['Cost', 'total_cost', 'money'],
    ['Status', 'status'],
  ],
  textFilters: [
    ['Serial number', 'serial_number', 'B0808450_  (_ = one char, % = any)'],
    ['Item code', 'item_code', 'CC-000123'],
  ],
  facetFilters: [
    ['Series', 'series', 'series'],
    ['Note type', 'note_type', 'note_type'],
    ['Seal', 'seal_color', 'seal_color'],
    ['District', 'fed_district', 'fed_district_letter'],
    ['Grade', 'grade', 'grade'],
    ['Series year', 'series_year', 'series_year'],
    ['Status', 'status', 'status'],
    ['Disposition', 'disposition', 'disposition'],
  ],
  // Named diagnostics. The code is the contract and appears in bookmarked
  // URLs; the wording comes from the API's `description`, so it is written
  // once on the server rather than twice.
  issueChecks: [
    'no_year',
    'no_country',
    'no_grade',
    'no_denomination',
    'mixed_marker',
    'zero_cost',
    'kind_unknown',
    'repeated_identity',
    'unreviewed',
    'star_mismatch',
    'malformed_serial',
    'near_duplicate_serial',
  ],
}

export const PAGE_SIZE = 50
