<template>
  <div>
    <div class="card section">
      <div class="card-title">日 K 线 · MA5/10/20/60 · 成交量</div>
      <div class="card-sub">stock_daily 表 · {{ tsCode }} · 最近 {{ bars.length }} 个交易日（红涨绿跌）</div>
      <ChartBox v-if="bars.length" :option="option" :height="520" />
      <div v-else class="muted loading">加载中…</div>
    </div>

    <div class="grid quote-grid section">
      <StatCard label="最新收盘" :value="lastBar ? lastBar.close.toFixed(2) : '—'" raw unit="元" />
      <StatCard
        label="当日涨跌" raw
        :value="lastBar ? (lastBar.pct_chg > 0 ? '+' : '') + lastBar.pct_chg.toFixed(2) + '%' : '—'"
        :tone="lastBar ? (lastBar.pct_chg > 0 ? 'up' : lastBar.pct_chg < 0 ? 'down' : '') : ''"
      />
      <StatCard label="当日成交量" :value="lastBar ? fmtCompact(lastBar.vol) : '—'" raw unit="手" />
      <StatCard label="当日成交额" :value="lastBar ? fmtCompact(lastBar.amount) : '—'" raw unit="千元" />
      <StatCard label="区间最高" :value="hi.toFixed(2)" raw unit="元" />
      <StatCard label="区间最低" :value="lo.toFixed(2)" raw unit="元" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import ChartBox from '../../components/ChartBox.vue'
import StatCard from '../../components/StatCard.vue'
import { getKline } from '../../api/analysis.js'
import { useThemeStore } from '../../stores/theme.js'
import { fmtCompact } from '../../utils/format.js'
import { baseOption, xAxis, yAxis, lineSeries, updown, chrome, seriesColors } from '../../utils/chart.js'

const props = defineProps({ tsCode: { type: String, required: true } })
const theme = useThemeStore()

const bars = ref([])

watch(() => props.tsCode, async (code) => {
  const data = await getKline(code)
  bars.value = data.bars
}, { immediate: true })

const lastBar = computed(() => bars.value[bars.value.length - 1] || null)
const hi = computed(() => (bars.value.length ? Math.max(...bars.value.map((b) => b.high)) : 0))
const lo = computed(() => (bars.value.length ? Math.min(...bars.value.map((b) => b.low)) : 0))

const option = computed(() => {
  const c = chrome(theme.isDark)
  const colors = seriesColors(theme.isDark)
  const dates = bars.value.map((b) => b.trade_date)
  const ohlc = bars.value.map((b) => [b.open, b.close, b.low, b.high])

  // 简单均线（前端由收盘价直接计算，与后端 ma5/ma10/ma20/ma60 口径一致）
  const ma = (n) => bars.value.map((_, i) => {
    if (i < n - 1) return null
    let s = 0
    for (let j = i - n + 1; j <= i; j++) s += bars.value[j].close
    return +(s / n).toFixed(2)
  })

  const vols = bars.value.map((b, i) => ({
    value: b.vol,
    itemStyle: { color: b.close >= b.open ? c.up : c.down, opacity: 0.75 },
  }))

  return {
    ...baseOption(theme.isDark),
    tooltip: { ...baseOption(theme.isDark).tooltip, trigger: 'axis', axisPointer: { type: 'cross' } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { ...baseOption(theme.isDark).legend, data: ['K线', 'MA5', 'MA10', 'MA20', 'MA60'] },
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
      { type: 'inside', xAxisIndex: [0, 1], start: 55, end: 100 },
      {
        type: 'slider', xAxisIndex: [0, 1], start: 55, end: 100,
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
      lineSeries('MA5', ma(5), { color: colors[0], lineStyle: { width: 1.4 } }),
      lineSeries('MA10', ma(10), { color: colors[1], lineStyle: { width: 1.4 } }),
      lineSeries('MA20', ma(20), { color: colors[2], lineStyle: { width: 1.4 } }),
      lineSeries('MA60', ma(60), { color: colors[3], lineStyle: { width: 1.4 } }),
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

.quote-grid {
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
}
</style>
