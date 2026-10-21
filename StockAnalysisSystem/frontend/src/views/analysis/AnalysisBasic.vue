<template>
  <div>
    <div class="card section">
      <div class="card-title">最新估值快照（stock_daily_basic）</div>
      <div class="card-sub">{{ tsCode }} · {{ basic.trade_date }}</div>
      <p v-if="basicError" role="alert">{{ basicError }}</p>
      <p v-else-if="basicLoading" role="status">估值数据加载中…</p>
      <p v-else-if="basicEmpty" role="status">暂无该股票估值数据；不会自动采集或使用演示数据。</p>
      <div v-if="basic.ts_code" class="grid basic-grid">
        <StatCard label="收盘价" :value="String(basic.close ?? '—')" raw unit="元" />
        <StatCard label="换手率" :value="String(basic.turnover_rate ?? '—')" raw unit="%" />
        <StatCard label="PE" :value="String(basic.pe ?? '—')" raw />
        <StatCard label="PE(TTM)" :value="String(basic.pe_ttm ?? '—')" raw />
        <StatCard label="PB" :value="String(basic.pb ?? '—')" raw />
        <StatCard label="PS" :value="String(basic.ps ?? '—')" raw />
        <StatCard label="总市值" :value="fmtWanToYi(basic.total_mv)" raw unit="亿元" />
      </div>
    </div>

    <div class="card section">
      <div class="card-title">日线行情（stock_daily，最近 30 个交易日）</div>
      <p v-if="klineError" role="alert">{{ klineError }}</p>
      <DataTable :columns="dailyColumns" :rows="rows" :page-size="12" />
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import StatCard from '../../components/StatCard.vue'
import DataTable from '../../components/DataTable.vue'
import { getKline, getDailyBasicRow } from '../../api/analysis.js'
import { fmtCompact, fmtWanToYi } from '../../utils/format.js'
import { createGenerationGuard, settleDetailRequest } from '../../utils/prediction.js'
import { valuationErrorMessage } from '../../utils/valuation.js'

const props = defineProps({ tsCode: { type: String, required: true } })

const basic = ref({})
const rows = ref([])
const basicError = ref(''), basicEmpty = ref(false), basicLoading = ref(false), klineError = ref('')
const guard = createGenerationGuard()

const dailyColumns = [
  { key: 'trade_date', label: 'trade_date', width: '100px' },
  { key: 'open', label: 'open', align: 'right', type: 'number' },
  { key: 'high', label: 'high', align: 'right', type: 'number' },
  { key: 'low', label: 'low', align: 'right', type: 'number' },
  { key: 'close', label: 'close', align: 'right', type: 'number' },
  { key: 'pre_close', label: 'pre_close', align: 'right', type: 'number' },
  { key: 'change', label: 'change', align: 'right', type: 'updown' },
  { key: 'pct_chg', label: 'pct_chg(%)', align: 'right', type: 'updown' },
  { key: 'vol', label: 'vol(手)', align: 'right', format: (v) => fmtCompact(v) },
  { key: 'amount', label: 'amount(千元)', align: 'right', format: (v) => fmtCompact(v) },
]

watch(() => props.tsCode, async (code) => {
  const run = guard.next()
  basic.value = {}; rows.value = []; basicError.value = ''; klineError.value = ''; basicEmpty.value = false; basicLoading.value = true
  await Promise.all([
    settleDetailRequest(getKline(code, { signal: run.signal }), run, {
      success: kline => { rows.value = [...kline.bars].reverse().slice(0, 30) },
      error: error => { klineError.value = error.message }, settled: () => {},
    }),
    settleDetailRequest(getDailyBasicRow(code, undefined, { signal: run.signal }), run, {
      success: value => { basic.value = value || {}; basicEmpty.value = value === null },
      error: error => { basicError.value = valuationErrorMessage(error) },
      settled: () => { basicLoading.value = false },
    }),
  ])
}, { immediate: true })
onBeforeUnmount(() => guard.cancel())
</script>

<style scoped>
.basic-grid {
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
}
</style>
