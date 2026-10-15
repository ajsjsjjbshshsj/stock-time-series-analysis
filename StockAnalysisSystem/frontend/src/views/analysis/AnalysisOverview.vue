<template>
  <div>
    <div class="card section">
      <div class="card-title">
        K 线 · {{ periodLabel }} · MA5/10/20/60 · 成交量
        <span class="period-chips" role="group" aria-label="K 线周期切换">
          <button
            v-for="p in KLINE_PERIODS" :key="p.key"
            class="period-btn" :class="{ active: period === p.key }"
            :aria-pressed="period === p.key"
            @click="period = p.key"
          >{{ p.label }}</button>
        </span>
        <button @click="load" aria-label="刷新行情">刷新行情</button>
      </div>
      <div class="card-sub">
        {{ live ? '真实 MySQL stock_daily' : '演示 stock_daily' }} · {{ tsCode }} ·
        日线 {{ bars.length }} 根 → {{ periodLabel }} {{ displayBars.length }} 根（红涨绿跌 · 均线由浏览器本地计算，不依赖指标服务）
        <template v-if="!live && period !== 'day'">· 演示数据仅 {{ bars.length }} 根日线，周期线样本有限</template>
      </div>
      <p v-if="loading" role="status">K 线加载中…</p>
      <p v-if="barError" role="alert">K 线不可用：{{ barError }} <button @click="load">重试</button></p>
      <ChartBox v-if="displayBars.length" :option="option" :height="520" />
      <div v-else-if="!loading && !barError" class="muted loading">暂无 K 线数据</div>
    </div>

    <div class="grid quote-grid section">
      <StatCard label="最新收盘" :value="lastBar ? lastBar.close.toFixed(2) : '—'" raw unit="元" />
      <StatCard
        label="当日涨跌" raw
        :value="lastBar?.pct_chg != null ? (lastBar.pct_chg > 0 ? '+' : '') + lastBar.pct_chg.toFixed(2) + '%' : '—'"
        :tone="lastBar ? (lastBar.pct_chg > 0 ? 'up' : lastBar.pct_chg < 0 ? 'down' : '') : ''"
      />
      <StatCard label="当日成交量" :value="lastBar ? fmtCompact(lastBar.vol) : '—'" raw unit="手" />
      <StatCard label="当日成交额" :value="lastBar ? fmtCompact(lastBar.amount) : '—'" raw unit="千元" />
      <StatCard label="区间最高" :value="bars.length ? hi.toFixed(2) : '—'" raw unit="元" />
      <StatCard label="区间最低" :value="bars.length ? lo.toFixed(2) : '—'" raw unit="元" />
    </div>
  </div>
</template>

<script setup>
/**
 * 综合概览 K 线主图（对齐同花顺）：
 * - 日/周/月/年周期切换，周期 bar 由日线本地聚合（utils/kline.js）；
 * - MA5/10/20/60 均线叠加在蜡烛图上，两种数据模式统一用 sma() 浏览器本地计算，
 *   不再请求 indicators 接口（ClickHouse 未部署时 503，均线曾整段缺失）；
 * - live 模式拉取长历史（limit=2000，后端上限）保证周/月/年线样本充足；
 * - 成交量副图与 dataZoom 保留，缩放窗口默认展示最近约 120 根。
 */
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import ChartBox from '../../components/ChartBox.vue'
import StatCard from '../../components/StatCard.vue'
import { getKline } from '../../api/analysis.js'
import { dataSource } from '../../api/client.js'
import { KLINE_PERIODS, aggregateBars } from '../../utils/kline.js'
import { sma } from '../../utils/indicators.js'
import { useThemeStore } from '../../stores/theme.js'
import { fmtCompact, fmtMonthDay } from '../../utils/format.js'
import { baseOption, xAxis, yAxis, lineSeries, chrome, seriesColors } from '../../utils/chart.js'

const props = defineProps({ tsCode: { type: String, required: true } })
const theme = useThemeStore()

/* live 拉长历史（后端 series bounds 上限 2000）；演示模式 getKline 忽略 limit，固定 260 根日线 */
const LIVE_LIMIT = 2000

const bars = ref([]) // 日线（升序，trade_date 为 YYYYMMDD）
const period = ref('day')
const live = computed(() => dataSource.value === 'hybrid')
const loading = ref(false)
const barError = ref('')
let generation = 0, controller

async function load() {
  controller?.abort(); controller = new AbortController()
  const run = ++generation, code = props.tsCode
  bars.value = []; barError.value = ''
  loading.value = true
  const options = { signal: controller.signal, ...(live.value ? { limit: LIVE_LIMIT } : {}) }
  await getKline(code, options)
    .then((data) => { if (run === generation) bars.value = data.bars })
    .catch((e) => { if (run === generation && e.name !== 'AbortError') barError.value = e.message })
    .finally(() => { if (run === generation) loading.value = false })
}

watch(() => props.tsCode, load, { immediate: true })
onBeforeUnmount(() => { generation++; controller?.abort() })

/* 周期聚合 + 本地均线；切换周期无需重新请求 */
const displayBars = computed(() => aggregateBars(bars.value, period.value))
const periodLabel = computed(() => KLINE_PERIODS.find((p) => p.key === period.value)?.label ?? '日K')
const lastBar = computed(() => bars.value[bars.value.length - 1] || null)
const hi = computed(() => (bars.value.length ? Math.max(...bars.value.map((b) => b.high)) : 0))
const lo = computed(() => (bars.value.length ? Math.min(...bars.value.map((b) => b.low)) : 0))

const option = computed(() => {
  const c = chrome(theme.isDark)
  const colors = seriesColors(theme.isDark)
  const list = displayBars.value
  const dates = list.map((b) => b.period_label ?? fmtMonthDay(b.trade_date))
  const ohlc = list.map((b) => [b.open, b.close, b.low, b.high])
  const closes = list.map((b) => b.close)
  const vols = list.map((b) => ({
    value: b.vol,
    itemStyle: { color: b.close >= b.open ? c.up : c.down, opacity: 0.75 },
  }))

  // 均线本地计算；样本不足（如年 K 仅几根）导致全 null 的均线整条隐藏，图例同步
  const maSeries = [5, 10, 20, 60]
    .map((n, i) => lineSeries(`MA${n}`, sma(closes, n).map((v) => (v == null ? null : +v.toFixed(2))), { color: colors[i], lineStyle: { width: 1.4 } }))
    .filter((s) => s.data.some((v) => v != null))

  // 默认缩放窗口：bar 数超过 120 时只展示最近约 120 根，否则全展示
  const zoomStart = list.length > 120 ? +((1 - 120 / list.length) * 100).toFixed(1) : 0

  return {
    ...baseOption(theme.isDark),
    tooltip: { ...baseOption(theme.isDark).tooltip, trigger: 'axis', axisPointer: { type: 'cross' } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { ...baseOption(theme.isDark).legend, data: ['K线', ...maSeries.map((s) => s.name)] },
    grid: [
      { left: 8, right: 18, top: 34, height: '56%', containLabel: true },
      { left: 8, right: 18, top: '74%', height: '15%', containLabel: true },
    ],
    xAxis: [
      xAxis(theme.isDark, { data: dates, boundaryGap: true, axisPointer: { label: { show: false } } }),
      xAxis(theme.isDark, { data: dates, gridIndex: 1, axisLabel: { show: false }, boundaryGap: true }),
    ],
    yAxis: [
      yAxis(theme.isDark, { scale: true, splitNumber: 4 }),
      yAxis(theme.isDark, { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } }),
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: zoomStart, end: 100 },
      {
        type: 'slider', xAxisIndex: [0, 1], start: zoomStart, end: 100,
        bottom: 2, height: 18,
        borderColor: c.grid, backgroundColor: 'transparent',
        fillerColor: theme.isDark ? 'rgba(57,135,229,0.14)' : 'rgba(42,120,214,0.10)',
        handleStyle: { color: colors[0] },
        textStyle: { color: c.muted, fontSize: 10 },
      },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: ohlc,
        itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down },
      },
      ...maSeries,
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: vols, barMaxWidth: 8,
      },
    ],
  }
})
</script>

<style scoped>
.loading {
  padding: 40px;
  text-align: center;
}

.period-chips {
  display: inline-flex;
  gap: 4px;
  margin-left: 10px;
  vertical-align: middle;
}

.period-btn {
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-2);
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  line-height: 1.4;
}

.period-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.period-btn.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.quote-grid {
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
}
</style>
