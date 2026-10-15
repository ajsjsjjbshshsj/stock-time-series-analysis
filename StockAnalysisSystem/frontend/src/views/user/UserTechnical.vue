<template>
  <div>
    <div class="card section">
      <div class="card-title">技术指标 · 浏览器本地计算</div>
      <div class="card-sub">
        基于{{ live ? '真实 MySQL 日线' : '演示日线' }} {{ tsCode }} · 全量 {{ bars.length }} 个交易日参与预热，展示最近 {{ winLen }} 日
      </div>

      <p v-if="loading" role="status">指标计算中…</p>
      <p v-else-if="error" role="alert">指标不可用：{{ error }} <button @click="load">重试</button></p>

      <template v-else-if="winFeats.length">
        <div class="ind-tabs" role="group" aria-label="指标切换">
          <button
            v-for="p in PANELS" :key="p.key"
            class="ind-tab" :class="{ active: panel === p.key }"
            :aria-pressed="panel === p.key"
            @click="panel = p.key"
          >{{ p.label }}</button>
        </div>

        <ChartBox :option="option" :height="340" />

        <div class="grid stat-grid section">
          <StatCard v-for="s in latestStats" :key="s.label" v-bind="s" />
        </div>

        <p v-if="signals.length" class="signal-line" :class="signals[0].tone" role="status">
          <Icon name="activity" /> {{ signals.map((s) => s.text).join(' · ') }}
        </p>
        <p class="muted signal-note">信号由历史数据计算，仅供学习参考，不构成投资建议。</p>
      </template>

      <div v-else-if="!loading && !error" class="muted empty">暂无日线数据，无法计算指标</div>
    </div>
  </div>
</template>

<script setup>
/**
 * 用户端技术指标面板：拉取日线（live 走 8083 kline，mock 走演示数据）后，
 * 在浏览器本地用 utils/indicators.js 内核计算全部指标——不依赖 8083 的
 * indicators 接口（该接口依赖 ClickHouse，未部署时 503）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ChartBox from '../../components/ChartBox.vue'
import StatCard from '../../components/StatCard.vue'
import Icon from '../../components/Icon.vue'
import { getKline } from '../../api/analysis.js'
import { dataSource } from '../../api/client.js'
import { computeIndicators } from '../../utils/indicators.js'
import { createGenerationGuard, settleDetailRequest } from '../../utils/prediction.js'
import { useThemeStore } from '../../stores/theme.js'
import { fmtNum, fmtCompact, fmtMonthDay } from '../../utils/format.js'
import { baseOption, xAxis, yAxis, lineSeries, chrome } from '../../utils/chart.js'

const props = defineProps({ tsCode: { type: String, required: true } })
const theme = useThemeStore()

const PANELS = [
  { key: 'macd', label: 'MACD' },
  { key: 'rsi', label: 'RSI' },
  { key: 'kdj', label: 'KDJ' },
  { key: 'boll', label: 'BOLL 布林带' },
  { key: 'vol', label: '量能' },
]
const WINDOW = 120

const live = computed(() => dataSource.value === 'hybrid')
const panel = ref('macd')
const bars = ref([])
const feats = ref([])
const loading = ref(false)
const error = ref('')

const guard = createGenerationGuard()

async function load() {
  const code = props.tsCode
  const run = guard.next()
  loading.value = true
  error.value = ''
  bars.value = []
  feats.value = []
  await settleDetailRequest(getKline(code, { signal: run.signal }), run, {
    success: (data) => {
      bars.value = data.bars
      feats.value = computeIndicators(data.bars, { tsCode: code })
    },
    error: (e) => { error.value = e.message },
    settled: () => { loading.value = false },
  })
}

watch(() => props.tsCode, load, { immediate: true })
onBeforeUnmount(() => guard.cancel())

const winBars = computed(() => bars.value.slice(-WINDOW))
const winFeats = computed(() => feats.value.slice(-WINDOW))
const winLen = computed(() => winBars.value.length)
const dates = computed(() => winBars.value.map((b) => fmtMonthDay(b.trade_date)))
const latest = computed(() => winFeats.value[winFeats.value.length - 1] || null)

function pick(key, digits = 2) {
  return winFeats.value.map((f) => (f[key] == null ? null : +Number(f[key]).toFixed(digits)))
}

/* ── 最新值卡片（StatCard 均为 raw 字符串，避免千分位取整） ── */

const latestStats = computed(() => {
  const f = latest.value
  if (!f) return []
  const num = (v, d = 2) => (v == null ? '—' : fmtNum(v, d))
  const signTone = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
  switch (panel.value) {
    case 'macd':
      return [
        { label: 'DIF（快线）', value: num(f.macd_dif, 3), raw: true, tone: signTone(f.macd_dif) },
        { label: 'DEA（慢线）', value: num(f.macd_dea, 3), raw: true, tone: signTone(f.macd_dea) },
        { label: 'MACD 柱', value: num(f.macd_hist, 3), raw: true, tone: signTone(f.macd_hist), sub: f.macd_hist >= 0 ? '红柱 · 多头动能' : '绿柱 · 空头动能' },
      ]
    case 'rsi':
      return [
        {
          label: 'RSI(14)', value: num(f.rsi, 2), raw: true,
          tone: f.rsi >= 70 ? 'warning' : f.rsi <= 30 ? 'accent' : '',
          sub: f.rsi >= 70 ? '超买区间（≥70）' : f.rsi <= 30 ? '超卖区间（≤30）' : '中性区间（30~70）',
        },
        { label: 'RSI 超买标记', value: f.rsi_overbought ? '触发' : '未触发', raw: true, tone: f.rsi_overbought ? 'warning' : '' },
        { label: 'RSI 超卖标记', value: f.rsi_oversold ? '触发' : '未触发', raw: true, tone: f.rsi_oversold ? 'accent' : '' },
      ]
    case 'kdj':
      return [
        { label: 'K 值', value: num(f.kdj_k, 2), raw: true },
        { label: 'D 值', value: num(f.kdj_d, 2), raw: true },
        { label: 'J 值', value: num(f.kdj_j, 2), raw: true, tone: f.kdj_j > 100 ? 'warning' : f.kdj_j < 0 ? 'accent' : '' },
      ]
    case 'boll':
      return [
        { label: '上轨', value: num(f.bb_upper), raw: true },
        { label: '中轨（MA20）', value: num(f.bb_middle), raw: true },
        { label: '下轨', value: num(f.bb_lower), raw: true },
        { label: '带宽', value: f.bb_width == null ? '—' : (f.bb_width * 100).toFixed(2) + '%', raw: true, sub: '（上轨−下轨）/ 中轨' },
      ]
    case 'vol':
      return [
        { label: '最新成交量', value: fmtCompact(winBars.value.at(-1)?.vol), raw: true, unit: '手' },
        { label: '量 MA5', value: f.vol_ma5 == null ? '—' : fmtCompact(f.vol_ma5), raw: true, unit: '手' },
        { label: '量比（量/量MA5）', value: num(f.volume_ratio), raw: true, tone: f.volume_ratio > 1.5 ? 'warning' : '' },
      ]
    default:
      return []
  }
})

/* ── 最新技术信号 ─────────────────────────────────────── */

const signals = computed(() => {
  const f = latest.value
  if (!f) return []
  const out = []
  if (f.golden_cross === 1) out.push({ text: 'MA5 上穿 MA10（金叉），短期转多', tone: 'up' })
  if (f.death_cross === 1) out.push({ text: 'MA5 下穿 MA10（死叉），短期转空', tone: 'down' })
  if (f.macd_golden_cross === 1) out.push({ text: 'MACD 金叉（DIF 上穿 DEA）', tone: 'up' })
  if (f.rsi_overbought === 1) out.push({ text: `RSI ${fmtNum(f.rsi, 1)} 超买，注意回调风险`, tone: 'warning' })
  if (f.rsi_oversold === 1) out.push({ text: `RSI ${fmtNum(f.rsi, 1)} 超卖，可能超跌`, tone: 'accent' })
  return out
})

/* ── 图表 ─────────────────────────────────────────────── */

const option = computed(() => {
  const isDark = theme.isDark
  const c = chrome(isDark)
  const axisLabel = { color: c.muted, fontSize: 10, interval: Math.max(1, Math.floor(dates.value.length / 6)) }
  const base = {
    ...baseOption(isDark),
    tooltip: { ...baseOption(isDark).tooltip, trigger: 'axis' },
    grid: { left: 8, right: 14, top: 30, bottom: 2, containLabel: true },
    xAxis: xAxis(isDark, { data: dates.value, axisLabel }),
    yAxis: yAxis(isDark, { scale: true, splitNumber: 4, axisLabel: { fontSize: 10 } }),
  }

  switch (panel.value) {
    case 'macd': {
      const hist = pick('macd_hist')
      return {
        ...base,
        series: [
          {
            name: 'MACD柱', type: 'bar', barMaxWidth: 5,
            data: hist.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? c.up : c.down } })),
          },
          lineSeries('DIF', pick('macd_dif', 3)),
          lineSeries('DEA', pick('macd_dea', 3)),
        ],
      }
    }
    case 'rsi':
      return {
        ...base,
        yAxis: yAxis(isDark, { min: 0, max: 100, splitNumber: 4, axisLabel: { fontSize: 10 } }),
        series: [
          lineSeries('RSI(14)', pick('rsi'), {
            markLine: {
              symbol: 'none', silent: true,
              lineStyle: { type: 'dashed', color: c.muted },
              label: { color: c.muted, fontSize: 10 },
              data: [{ yAxis: 70, name: '超买' }, { yAxis: 30, name: '超卖' }],
            },
          }),
        ],
      }
    case 'kdj':
      return {
        ...base,
        series: [lineSeries('K', pick('kdj_k')), lineSeries('D', pick('kdj_d')), lineSeries('J', pick('kdj_j'))],
      }
    case 'boll':
      return {
        ...base,
        series: [
          lineSeries('收盘价', winBars.value.map((b) => b.close), { lineStyle: { width: 1.6 } }),
          lineSeries('上轨', pick('bb_upper'), { lineStyle: { width: 1.2, type: 'dashed' } }),
          lineSeries('中轨', pick('bb_middle'), { lineStyle: { width: 1.2 } }),
          lineSeries('下轨', pick('bb_lower'), { lineStyle: { width: 1.2, type: 'dashed' } }),
        ],
      }
    case 'vol':
      return {
        ...base,
        yAxis: yAxis(isDark, {
          splitNumber: 4,
          axisLabel: { fontSize: 10, formatter: (v) => (v >= 10000 ? (v / 10000).toFixed(0) + '万' : v) },
        }),
        series: [
          {
            name: '成交量', type: 'bar', barMaxWidth: 5,
            data: winBars.value.map((b) => ({
              value: b.vol,
              itemStyle: { color: b.close >= b.open ? c.up : c.down, opacity: 0.7 },
            })),
          },
          lineSeries('量MA5', pick('vol_ma5', 0)),
          lineSeries('量MA10', pick('vol_ma10', 0)),
        ],
      }
    default:
      return base
  }
})
</script>

<style scoped>
.ind-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.ind-tab {
  padding: 5px 14px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-2);
  font-size: 12.5px;
  cursor: pointer;
}

.ind-tab:hover {
  color: var(--text-1);
  border-color: var(--border-strong);
}

.ind-tab.active {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: var(--accent);
  font-weight: 650;
}

.stat-grid {
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  margin-top: 14px;
}

.signal-line {
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  padding: 9px 14px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--surface-2);
}

.signal-line svg {
  width: 15px;
  height: 15px;
  flex: none;
}

.signal-line.up { color: var(--up); }
.signal-line.down { color: var(--down); }
.signal-line.warning { color: var(--warning); }
.signal-line.accent { color: var(--accent); }

.signal-note {
  margin-top: 8px;
  font-size: 11.5px;
}

.empty {
  padding: 40px;
  text-align: center;
}
</style>
