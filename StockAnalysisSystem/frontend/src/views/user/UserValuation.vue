<template>
  <section v-if="row || loading || error || empty" class="card val-card" aria-label="估值快照">
    <div class="val-head">
      <span class="val-title">估值快照</span>
      <span class="val-date muted num">{{ row?.trade_date || '…' }}</span>
      <span v-if="error" class="val-err muted">{{ error }}</span>
    </div>
    <div v-if="row" class="val-grid">
      <div v-for="item in items" :key="item.k" class="val-item">
        <span class="muted">{{ item.k }}</span>
        <b class="num" :class="item.cls">{{ item.v }}</b>
      </div>
    </div>
    <div v-else-if="loading" class="muted val-state">估值数据加载中…</div>
    <div v-else-if="empty" class="muted val-state">暂无该股票估值数据；不会自动采集或使用演示数据。</div>
  </section>
</template>

<script setup>
/**
 * 用户端估值快照卡：股票信息卡下方的紧凑一行指标
 * （PE / PE(TTM) / PB / 换手率 / 总市值 / PS，来自 stock_daily_basic）。
 * live 模式请求 8083 的 /api/analysis/daily-basic/{tsCode}（最近交易日行），
 * 演示模式由 mock 生成器提供；真实缺数据与接口故障分别显示，不降级为 mock。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getDailyBasicRow } from '../../api/analysis.js'
import { createGenerationGuard, settleDetailRequest } from '../../utils/prediction.js'
import { fmtWanToYi } from '../../utils/format.js'
import { valuationErrorMessage } from '../../utils/valuation.js'

const props = defineProps({ tsCode: { type: String, required: true } })

const row = ref(null)
const loading = ref(false)
const error = ref('')
const empty = ref(false)
const guard = createGenerationGuard()

const f2 = (v) => (v == null ? '—' : Number(v).toFixed(2))

const items = computed(() => {
  const r = row.value
  if (!r) return []
  return [
    { k: 'PE(TTM)', v: f2(r.pe_ttm) },
    { k: 'PE', v: f2(r.pe) },
    { k: 'PB', v: f2(r.pb) },
    { k: '换手率', v: r.turnover_rate == null ? '—' : `${Number(r.turnover_rate).toFixed(2)}%` },
    { k: '总市值', v: fmtWanToYi(r.total_mv) },
    { k: 'PS', v: f2(r.ps) },
  ]
})

async function load() {
  const run = guard.next()
  loading.value = true
  error.value = ''
  empty.value = false
  row.value = null
  await settleDetailRequest(getDailyBasicRow(props.tsCode, undefined, { signal: run.signal }), run, {
    success: (data) => { row.value = data; empty.value = data === null },
    error: (e) => { error.value = valuationErrorMessage(e) },
    settled: () => { loading.value = false },
  })
}

watch(() => props.tsCode, load, { immediate: true })
onBeforeUnmount(() => guard.cancel())
</script>

<style scoped>
.val-card {
  padding: 12px 20px;
  margin-bottom: 14px;
}

.val-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.val-title {
  font-size: 13px;
  font-weight: 650;
  color: var(--text-1);
}

.val-date {
  font-size: 11.5px;
}

.val-err {
  font-size: 11.5px;
  color: var(--warning);
}

.val-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: 8px 14px;
}

.val-item {
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-size: 11px;
}

.val-item b {
  font-size: 15.5px;
  font-weight: 650;
  color: var(--text-1);
}

.val-state {
  font-size: 12px;
  padding: 4px 0;
}
</style>
