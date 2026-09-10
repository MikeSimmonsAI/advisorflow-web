/**
 * LIST KEYS THAT CANNOT COLLIDE.
 *
 * THE DEFECT THIS EXISTS TO PREVENT. Manager Today keyed its attention rows on
 * `opportunity_id`, and the server raises one row PER PROBLEM: a single deal
 * that has an overdue action and a failed video meeting produces two rows with
 * the same opportunity id. React then warned "Encountered two children with the
 * same key", and — worse than the warning — reconciled the two rows as one, so
 * the second problem could inherit the first one's state.
 *
 * The same shape appears three more times in this app:
 *
 *   - a pipeline deal row keyed on `d.id` where the projection payload calls
 *     the field `opportunity_id`, so EVERY row was keyed "undefined";
 *   - a team-today meeting that appears once per participant, so the same
 *     appointment id arrives two or three times in one flattened list;
 *   - any list keyed on an index, which is stable only until the list reorders.
 *
 * THE RULE. A key is built from every field that distinguishes one row from
 * another — never one id, never the index alone. The index is appended last as
 * a tiebreaker so that even a payload where every discriminator is null still
 * produces unique keys, because a React warning is a bug report and a duplicate
 * key that silently merges two rows is a data-integrity bug.
 */

/**
 * A stable, unique key from the parts that identify a row.
 *
 * `unknown` on purpose. Every caller is pulling values straight out of a JSON
 * payload, where the type is genuinely unknown until it is looked at — and a
 * narrower signature would only push a cast to each call site, which is where
 * the mistakes get made.
 *
 * @param parts  the row's discriminators, most significant first
 * @param index  the row's position, used only to break a remaining tie
 */
export function rowKey(parts: unknown[], index: number): string {
  const cleaned = parts
    .filter((p) => p !== null && p !== undefined && p !== ''
      && typeof p !== 'object' && typeof p !== 'function')
    .map((p) => String(p));
  // The index is always present. Two rows that are genuinely identical in
  // every field are still two rows, and they still have to render as two.
  return cleaned.length ? `${cleaned.join('|')}#${index}` : `row#${index}`;
}

/**
 * Deduplicate by identity, keeping the first occurrence and its order.
 *
 * Used where the server returns the same record under several parents — a
 * meeting listed once per attendee, for example — and the screen wants one row.
 */
export function dedupeBy<T>(rows: T[], identity: (row: T) => string | null): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const row of rows) {
    const id = identity(row);
    // A row with no identity cannot be proven to be a duplicate, so it is
    // kept. Dropping it would lose real data to protect against a maybe.
    if (id === null || id === '') { out.push(row); continue; }
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(row);
  }
  return out;
}
