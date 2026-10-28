/** Fail closed on mismatched result/date/counts; unknown proof stays unknown. */
export function validateCoverage(value, job) {
  const count = n => Number.isSafeInteger(n) && n >= 0 && n <= 10000
  const reason = s => typeof s === 'string' && /^[A-Z_]{1,64}$/.test(s)
  const fail = () => { throw new Error('缺失分数明细与所选结果不一致，请刷新后重试') }
  if (!value || value.provenance !== 'sealed_result_only' || value.prediction?.id !== job.id ||
      value.signal_date !== job.signal_date || value.selected_count !== job.selection.stock_count ||
      ![value.selected_count, value.scored_count, value.missing_count].every(count) ||
      value.scored_count + value.missing_count !== value.selected_count ||
      !value.reason_counts || typeof value.reason_counts !== 'object' || Array.isArray(value.reason_counts) ||
      Object.entries(value.reason_counts).some(([key, n]) => !reason(key) || !count(n) || !n) ||
      Object.values(value.reason_counts).reduce((a, b) => a + b, 0) !== value.missing_count ||
      !Array.isArray(value.items) || value.items.length > Math.min(100, value.missing_count) ||
      value.next_cursor !== null && (!count(value.next_cursor) || value.next_cursor < 1 || value.next_cursor >= value.missing_count)) fail()
  const seen = new Set(), pageCounts = {}
  for (const row of value.items) {
    if (!row || typeof row.ts_code !== 'string' || !/^[0-9]{6}\.(SH|SZ)$/.test(row.ts_code) ||
        seen.has(row.ts_code) || row.signal_date !== value.signal_date || !reason(row.reason) ||
        typeof row.reason_label !== 'string' || !row.reason_label.length || row.reason_label.length > 200 ||
        !['sealed_eligibility', 'not_recorded'].includes(row.provenance) ||
        (row.reason === 'UNVERIFIED') !== (row.provenance === 'not_recorded')) fail()
    seen.add(row.ts_code); pageCounts[row.reason] = (pageCounts[row.reason] || 0) + 1
    if (pageCounts[row.reason] > (value.reason_counts[row.reason] || 0)) fail()
  }
  return value
}
