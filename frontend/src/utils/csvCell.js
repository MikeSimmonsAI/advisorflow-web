/**
 * One CSV cell, safe to open in a spreadsheet.
 *
 * Two jobs: QUOTE every value (a comma or newline in a business name used to
 * shift every column after it), and NEUTRALISE FORMULAS. A cell that starts
 * with = @ + - (or a tab / carriage return) is executed by Excel and Sheets as
 * a formula - "=HYPERLINK(...)" in a scraped business name or an audit detail
 * becomes a live link or worse when somebody opens the export (OWASP "CSV
 * injection"). Such a value is prefixed with an apostrophe, which spreadsheets
 * treat as "this is text". A plain number or phone ("+1 214 555 0100",
 * "-12.5") is left alone.
 */
export function csvCell(value) {
  let s = value == null ? '' : String(value)
  if (/^[=@\t\r]/.test(s) || (/^[+-]/.test(s) && !/^[+-][\d\s().-]*$/.test(s))) s = "'" + s
  return '"' + s.replace(/"/g, '""') + '"'
}

export function csvRow(values) {
  return values.map(csvCell).join(',')
}
