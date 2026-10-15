<template>
  <div>
    <div class="card section">
      <div class="card-title">多股对比 · 归一化累计收益</div>
      <div class="card-sub">
        以共同交易日窗口的首日收盘为基准（0%），基于{{ live ? '真实 MySQL 日线' : '演示日线' }}本地计算
        <template v-if="common.length">· 共同窗口 {{ fmtMonthDay(common[0]) }} ~ {{ fmtMonthDay(common.at(-1)) }}（{{ common.length }} 个交易日）</template>
      </div>

      <!-- 已选股票 chips -->
      <div class="cmp-chips">
        <span v-for="code in selected" :key="code" class="cmp-chip">
          <span class="chip-name">{{ nameOf(code) }}<code class="num">{{ code }}</code></span>
          <button class="chip-x" :aria-label="`移出对比 ${nameOf(code)}`" @click="removeStock(code)">
            <Icon name="x" />
          </button>
        </span>
        <span v-if="selected.length < MAX" class="cmp-add">
          <label class="sr-only" for="compare-add">添加对比股票</label>
          <StockSearch
            id="compare-add"
            v-model="pending"
            :stocks="pool"
            :disabled="poolLoading || !pool.length"
            placeholder="搜索股票加入对比"
          />
          <button class="add-btn" :disabled="!canAdd" @click="addPending">加入对比</button>
        </span>
      </div>
      <p v-if="poolError" role="alert" class="state-line">股票池加载失败：{{ poolError }} <button @click="fetchPool">重试</button></p>
      <p v-if="selected.length >= MAX" class="muted hint-line">最多同时对比 {{ MAX }} 只股票，可先移出一只再添加。</p>

      <p v-if="loading" role="status" class="state-line">对比数据计算中…</p>
      <p v-else-if="error" role="alert" class="state-line">{{ error }} <button @click="loadCompare">重试</button></p>
      <template v-else-if="selected.length >= 2 && common.length >= 2">
        <ChartBox :option="option" :height="380" />
      </template>
      <p v-else-if="!loading && !error" class="state-line muted">请添加至少 2 只股票开始对比（最多 {{ MAX }} 只）。</p>
    </div>

    <div v-if="rows.length" class="card section">
      <div class="card-title">对比指标（共同窗口内）</div>
      <DataTable :columns="COMPARE_COLS" :rows="rows" row-key="ts_code" />
      <p class="muted hint-line">年化波动率 = 日收益率标准差 × √252；最大回撤基于收盘价峰值。仅供学习参考，不构成投资建议。</p>
    </div>
  </div>
</template>

<script setup>
/**
 * 用户端多股对比：最多 4 只股票，按共同交易日窗口对齐，
 * 归一化累计收益曲线叠加 + 区间收益/年化波动/最大回撤对比表。
 * 全部指标由 utils/indicators.js 本地计算，live/mock 通用。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChartBox from '../../components/ChartBox.vue'
import DataTable from '../../components/DataTable.vue'
import StockSearch from '../../components/StockSearch.vue'
import Icon from '../../components/Icon.vue'
import { getKline, getStockPoolCached } from '../../api/analysis.js'
import { dataSource } from '../../api/client.js'
import { computeIndicators, computeStatistics } from '../../utils/indicators.js'
import { createGenerationGuard, settleDetailRequest } from '../../utils/prediction.js'
import { useThemeStore } from '../../stores/theme.js'
import { fmtNum, fmtPctRaw, fmtMonthDay } from '../../utils/format.js'
import { baseOption, xAxis, yAxis, lineSeries, chrome } from '../../utils/chart.js'

const props = defineProps({ tsCode: { type: String, required: true } })
const theme = useThemeStore()

const MAX = 4

const live = computed(() => dataSource.value === 'hybrid')
const pool = ref([])
const poolLoading = ref(false)
const poolError = ref('')
const pending = ref('')
const selected = ref(props.tsCode ? [props.tsCode] : [])

const barsMap = ref({}) // { ts_code: bars[] }
const common = ref([]) // 共同交易日（升序）
const rows = ref([])
const loading = ref(false)
const error = ref('')

const guard = createGenerationGuard()

async function fetchPool() {
  poolLoading.value = true
  poolError.value = ''
  try {
    // live 模式 5000+ 行股票池走模块级缓存（与 UserMarket 共享），只拉一次
    pool.value = await getStockPoolCached()
  } catch (e) {
    poolError.value = e.message
  } finally {
    poolLoading.value = false
  }
}

onMounted(fetchPool)

// 主选中股变化时：对比列表为空则以其为起点
watch(() => props.tsCode, (code) => {
  if (!selected.value.length && code) selected.value = [code]
})

const canAdd = computed(() =>
  !!pending.value && !selected.value.includes(pending.value) && selected.value.length < MAX)

function addPending() {
  if (!canAdd.value) return
  selected.value = [...selected.value, pending.value]
  pending.value = ''
}

function removeStock(code) {
  selected.value = selected.value.filter((c) => c !== code)
}

function nameOf(code) {
  return pool.value.find((s) => s.ts_code === code)?.name || code
}

async function loadCompare() {
  const codes = [...selected.value]
  const { signal, current } = guard.next()
  error.value = ''
  if (codes.length < 2) {
    barsMap.value = {}
    common.value = []
    rows.value = []
    loading.value = false
    return
  }
  loading.value = true
  try {
    const results = await Promise.all(codes.map((c) => getKline(c, { signal })))
    if (!current()) return
    const map = Object.fromEntries(results.map((r) => [r.ts_code, r.bars]))
    // 共同交易日交集（各股上市时间不同，取都有数据的日期）
    const sets = codes.map((c) => new Set(map[c].map((b) => b.trade_date)))
    const commonDates = map[codes[0]]
      .map((b) => b.trade_date)
      .filter((d) => sets.every((s) => s.has(d)))
    barsMap.value = map
    common.value = commonDates
    rows.value = buildRows(codes, map, commonDates)
  } catch (e) {
    if (current() && e.name !== 'AbortError') {
      error.value = `对比数据不可用：${e.message}`
      rows.value = []
      common.value = []
    }
  } finally {
    if (current()) loading.value = false
  }
}

watch(selected, loadCompare, { immediate: true, deep: true })
onBeforeUnmount(() => guard.cancel())

function buildRows(codes, map, commonDates) {
  if (commonDates.length < 2) return []
  const dateSet = new Set(commonDates)
  return codes.map((code) => {
    const sub = map[code].filter((b) => dateSet.has(b.trade_date))
    const feats = computeIndicators(sub, { tsCode: code })
    const st = computeStatistics(sub, feats)
    const first = sub[0].close
    const last = sub[sub.length - 1].close
    return {
      ts_code: code,
      name: code, // 占位；显示时经 COMPARE_COLS 的 format 查池内名称（池加载后自动刷新）
      period_return: +(((last / first) - 1) * 100).toFixed(2),
      annual_vol: +(st.descriptive.std * Math.sqrt(252)).toFixed(2),
      max_dd: st.drawdown.max_drawdown,
      close: last,
    }
  })
}

const COMPARE_COLS = [
  { key: 'name', label: '股票', align: 'left', format: (v, row) => nameOf(row.ts_code) },
  { key: 'ts_code', label: '代码', align: 'left' },
  { key: 'period_return', label: '区间收益', align: 'right', type: 'updown', format: (v) => fmtPctRaw(v) },
  { key: 'annual_vol', label: '年化波动率', align: 'right', type: 'number', format: (v) => fmtPctRaw(v) },
  { key: 'max_dd', label: '最大回撤', align: 'right', type: 'number', format: (v) => (v == null ? '—' : fmtPctRaw(v)) },
  { key: 'close', label: '最新收盘', align: 'right', type: 'number', format: (v) => fmtNum(v) },
]

/* 曲线：每只股票在共同窗口上的归一化累计收益（%） */
const curves = computed(() => {
  if (common.value.length < 2) return []
  return selected.value
    .filter((code) => barsMap.value[code])
    .map((code) => {
      const byDate = new Map(barsMap.value[code].map((b) => [b.trade_date, b.close]))
      const base = byDate.get(common.value[0])
      return {
        code,
        name: nameOf(code),
        data: common.value.map((d) => {
          const c = byDate.get(d)
          return c == null || !base ? null : +(((c / base) - 1) * 100).toFixed(2)
        }),
      }
    })
})

const option = computed(() => {
  const isDark = theme.isDark
  const c = chrome(isDark)
  return {
    ...baseOption(isDark),
    tooltip: {
      ...baseOption(isDark).tooltip,
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`),
    },
    grid: { left: 8, right: 18, top: 32, bottom: 2, containLabel: true },
    xAxis: xAxis(isDark, {
      data: common.value.map((d) => fmtMonthDay(d)),
      axisLabel: { color: c.muted, fontSize: 10, interval: Math.max(1, Math.floor(common.value.length / 6)) },
      boundaryGap: false,
    }),
    yAxis: yAxis(isDark, { scale: true, axisLabel: { fontSize: 10, formatter: '{value}%' } }),
    series: curves.value.map((s) => lineSeries(s.name, s.data)),
  }
})
</script>

<style scoped>
.cmp-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 10px 0 4px;
}

.cmp-chip {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface-2);
  overflow: hidden;
}

.chip-name {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 5px 4px 5px 12px;
  font-size: 12.5px;
  color: var(--text-1);
  font-weight: 600;
}

.chip-name code {
  font-size: 10.5px;
  color: var(--text-3);
  font-weight: 400;
}

.chip-x {
  display: grid;
  place-items: center;
  width: 26px;
  background: transparent;
  border: none;
  color: var(--text-3);
  cursor: pointer;
}

.chip-x:hover {
  color: var(--critical);
}

.chip-x svg {
  width: 11px;
  height: 11px;
}

.cmp-add {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 320px;
  min-width: 280px;
  max-width: 480px;
}

.add-btn {
  flex: none;
  padding: 7px 14px;
  border-radius: 9px;
  border: 1px solid var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12.5px;
  font-weight: 650;
  cursor: pointer;
  white-space: nowrap;
}

.add-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.state-line {
  text-align: center;
  padding: 18px 0;
  font-size: 13px;
  color: var(--text-2);
}

.hint-line {
  font-size: 11.5px;
  margin-top: 8px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
