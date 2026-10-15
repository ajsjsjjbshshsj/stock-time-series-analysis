<template>
  <div>
    <p v-if="loading" role="status" class="state-line">市场概览加载中…</p>
    <p v-else-if="error" role="alert" class="state-line">{{ error }} <button @click="load">重试</button></p>

    <template v-else-if="ov && ov.total">
      <!-- 涨跌概览 -->
      <div class="card section">
        <div class="card-title">市场概览</div>
        <div class="card-sub">
          交易日 <span class="num">{{ ov.trade_date }}</span> · 共 {{ ov.total }} 只 ·
          基于{{ live ? '真实 MySQL 日线（stock_daily）' : '演示日线' }}
        </div>
        <div class="grid mkt-grid">
          <StatCard label="上涨家数" :value="String(ov.up_count)" raw tone="up" :sub="`占比 ${share(ov.up_count)}`" />
          <StatCard label="下跌家数" :value="String(ov.down_count)" raw tone="down" :sub="`平盘 ${ov.flat_count} 只`" />
          <StatCard label="平均涨跌幅" :value="fmtPctSigned(ov.avg_pct_chg)" raw :tone="signTone(ov.avg_pct_chg)" sub="全市场等权" />
          <StatCard label="市场成交额" :value="fmtYi(ov.total_amount)" raw unit="亿元" sub="沪深北合计" />
        </div>
        <div class="adv-bar" role="img" :aria-label="`上涨 ${ov.up_count} 家，下跌 ${ov.down_count} 家`">
          <span class="adv-up" :style="{ width: share(ov.up_count) }"></span>
          <span class="adv-flat" :style="{ width: share(ov.flat_count) }"></span>
          <span class="adv-down" :style="{ width: share(ov.down_count) }"></span>
        </div>
      </div>

      <!-- 行业涨跌幅 -->
      <div class="grid two-col section">
        <div class="card">
          <div class="card-title">行业涨幅 TOP10</div>
          <div class="card-sub">按行业等权平均涨跌幅</div>
          <ChartBox v-if="topIndustries.length" :option="industryOption(topIndustries)" :height="280" />
          <p v-else class="muted empty-line">暂无行业数据</p>
        </div>
        <div class="card">
          <div class="card-title">行业跌幅 TOP10</div>
          <div class="card-sub">按行业等权平均涨跌幅</div>
          <ChartBox v-if="bottomIndustries.length" :option="industryOption(bottomIndustries)" :height="280" />
          <p v-else class="muted empty-line">暂无行业数据</p>
        </div>
      </div>

      <!-- 涨跌幅排行 -->
      <div class="grid two-col section">
        <div class="card">
          <div class="card-title">涨幅榜 TOP10</div>
          <div class="card-sub">点击股票名可查看该股分析</div>
          <DataTable :columns="RANK_COLS" :rows="ov.top_stocks || []" row-key="ts_code">
            <template #cell-name="{ row }">
              <button
                class="pick-btn" :disabled="!pickable(row.ts_code)"
                :title="pickable(row.ts_code) ? '查看该股分析' : '该股暂未收录进可搜索股票池（如当日新股）'"
                @click="emit('pick', row.ts_code)"
              >{{ row.name || row.ts_code }}</button>
            </template>
          </DataTable>
        </div>
        <div class="card">
          <div class="card-title">跌幅榜 TOP10</div>
          <div class="card-sub">点击股票名可查看该股分析</div>
          <DataTable :columns="RANK_COLS" :rows="ov.bottom_stocks || []" row-key="ts_code">
            <template #cell-name="{ row }">
              <button
                class="pick-btn" :disabled="!pickable(row.ts_code)"
                :title="pickable(row.ts_code) ? '查看该股分析' : '该股暂未收录进可搜索股票池（如当日新股）'"
                @click="emit('pick', row.ts_code)"
              >{{ row.name || row.ts_code }}</button>
            </template>
          </DataTable>
        </div>
      </div>

      <p class="muted hint-line">行情数据可能存在延迟，仅供学习参考，不构成投资建议。</p>
    </template>

    <p v-else-if="!loading && !error" class="state-line muted">暂无市场数据。</p>
  </div>
</template>

<script setup>
/**
 * 用户端市场概览：最新交易日全市场聚合。
 * live 模式请求 8083 的 /api/analysis/market/overview（MySQL 单日聚合，走 trade_date 索引）；
 * 演示模式由 mock 全池聚合，契约一致。点击排行榜股票 emit('pick') 交回 UserAppView 切换主选股。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ChartBox from '../../components/ChartBox.vue'
import DataTable from '../../components/DataTable.vue'
import StatCard from '../../components/StatCard.vue'
import { getMarketOverview, getStockPoolCached } from '../../api/analysis.js'
import { dataSource } from '../../api/client.js'
import { createGenerationGuard, settleDetailRequest } from '../../utils/prediction.js'
import { useThemeStore } from '../../stores/theme.js'
import { fmtPctRaw, fmtNum } from '../../utils/format.js'
import { baseOption, xAxis, yAxis, chrome } from '../../utils/chart.js'

const emit = defineEmits(['pick'])
const theme = useThemeStore()

const live = computed(() => dataSource.value === 'hybrid')
const ov = ref(null)
const loading = ref(false)
const error = ref('')
const guard = createGenerationGuard()

/* 股票池（模块级缓存，与 UserCompare 共享）：用于判断排行榜股票是否可切换。
   池未加载/加载失败时放行点击，由 UserAppView.onPick 的池校验兜底。 */
const pool = ref([])
const pickable = (code) => !pool.value.length || pool.value.some((s) => s.ts_code === code)

async function load() {
  const run = guard.next()
  loading.value = true
  error.value = ''
  await settleDetailRequest(getMarketOverview({ signal: run.signal }), run, {
    success: (data) => { ov.value = data },
    error: (e) => { error.value = `市场概览加载失败：${e.message}` },
    settled: () => { loading.value = false },
  })
}

onMounted(() => {
  load()
  getStockPoolCached().then((p) => { pool.value = p }).catch(() => { /* 兜底见 pickable 注释 */ })
})
onBeforeUnmount(() => guard.cancel())

const share = (n) => `${(((n ?? 0) / (ov.value?.total || 1)) * 100).toFixed(1)}%`
const signTone = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
const fmtPctSigned = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${fmtPctRaw(v)}`)
/** amount 千元 → 亿元 */
const fmtYi = (thousand) => (thousand == null ? '—' : (Number(thousand) / 1e5).toFixed(0))

const topIndustries = computed(() => (ov.value?.industries || []).slice(0, 10))
const bottomIndustries = computed(() => (ov.value?.industries || []).slice(-10).reverse())

const RANK_COLS = [
  { key: 'name', label: '名称', align: 'left' },
  { key: 'ts_code', label: '代码', align: 'left' },
  { key: 'close', label: '收盘', align: 'right', type: 'number', format: (v) => fmtNum(v) },
  { key: 'pct_chg', label: '涨跌幅', align: 'right', type: 'updown', format: (v) => fmtPctSigned(v) },
  { key: 'amount', label: '成交额(亿)', align: 'right', type: 'number', format: (v) => (v == null ? '—' : (Number(v) / 1e5).toFixed(2)) },
]

function industryOption(list) {
  const c = chrome(theme.isDark)
  const base = baseOption(theme.isDark)
  const rows = [...list].reverse() // 横向条形自下而上，反转后第一名在顶部
  return {
    ...base,
    tooltip: {
      ...base.tooltip,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (v) => `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`,
    },
    grid: { left: 8, right: 44, top: 8, bottom: 2, containLabel: true },
    xAxis: xAxis(theme.isDark, {
      type: 'value',
      axisLabel: { color: c.muted, fontSize: 10, formatter: '{value}%' },
      splitLine: { lineStyle: { color: c.grid, type: 'dashed' } },
    }),
    yAxis: yAxis(theme.isDark, {
      type: 'category',
      data: rows.map((r) => r.industry),
      axisLabel: { color: c.muted, fontSize: 10.5 },
      splitLine: { show: false },
    }),
    series: [{
      type: 'bar',
      barMaxWidth: 13,
      data: rows.map((r) => ({
        value: r.avg_pct_chg,
        itemStyle: { color: r.avg_pct_chg >= 0 ? c.up : c.down, borderRadius: 3 },
      })),
      label: {
        show: true, position: 'right', fontSize: 9.5, color: c.muted,
        formatter: (p) => `${p.value > 0 ? '+' : ''}${Number(p.value).toFixed(2)}%`,
      },
    }],
  }
}
</script>

<style scoped>
.state-line {
  text-align: center;
  padding: 24px 0;
  font-size: 13.5px;
  color: var(--text-2);
}

.mkt-grid {
  grid-template-columns: repeat(4, 1fr);
}

@media (max-width: 900px) {
  .mkt-grid { grid-template-columns: repeat(2, 1fr); }
}

.two-col {
  grid-template-columns: 1fr 1fr;
}

@media (max-width: 1100px) {
  .two-col { grid-template-columns: 1fr; }
}

.adv-bar {
  display: flex;
  height: 10px;
  border-radius: 999px;
  overflow: hidden;
  margin-top: 14px;
  background: var(--surface-2);
}

.adv-up { background: var(--up); }
.adv-flat { background: var(--border-strong, var(--border)); }
.adv-down { background: var(--down); }

.pick-btn {
  background: transparent;
  border: none;
  padding: 0;
  color: var(--accent);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}

.pick-btn:hover {
  text-decoration: underline;
}

.pick-btn:disabled {
  color: var(--text-3);
  cursor: default;
  font-weight: 400;
}

.pick-btn:disabled:hover {
  text-decoration: none;
}

.empty-line {
  padding: 30px 0;
  text-align: center;
  font-size: 12.5px;
}

.hint-line {
  font-size: 11.5px;
  text-align: center;
}
</style>
