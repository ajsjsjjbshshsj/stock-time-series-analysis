/** Current-page filtering only: never infer that older catalog pages are empty. */
export function filterPublishedResults(items, { operation = '', algorithm = '' } = {}) {
  return items.filter(row => (!operation || row.metadata?.operation === operation)
    && (!algorithm || row.algorithm === algorithm))
}
