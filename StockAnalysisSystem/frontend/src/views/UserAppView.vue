<template>
  <div class="user-app">
    <header class="user-topbar">
      <div class="brand">
        <div class="brand-mark">S</div>
        <div class="brand-text">
          股票时序分析
          <small>用户版 · 智能选股与分析服务</small>
        </div>
      </div>
      <div class="topbar-actions">
        <span v-if="!live" class="demo-badge">演示数据 · 非真实行情</span>
        <button
          class="theme-btn"
          :title="theme.isDark ? '切换到浅色主题' : '切换到深色主题'"
          @click="theme.toggle()"
        >
          <Icon :name="theme.isDark ? 'sun' : 'moon'" />
        </button>
      </div>
    </header>

    <main class="user-content">
      <section class="hero">
        <h1>股票智能分析</h1>
        <p class="hero-sub muted">选择一只股票，查看行情概览、模型预测与风险提示</p>
        <div class="hero-search">
          <label class="sr-only" for="user-stock-search">选择股票</label>
          <StockSearch
            id="user-stock-search"
            v-model="tsCode"
            :stocks="pool"
            :disabled="loading || !pool.length"
          />
        </div>
      </section>

      <p v-if="loading" role="status" class="state-text">股票池加载中…</p>
      <p v-else-if="error" role="alert" class="state-text">{{ error }} <button @click="loadPool">重试</button></p>
      <p v-else-if="!pool.length" role="status" class="state-text">股票池为空，暂无可查询股票。</p>

      <template v-if="current && !loading && !error">
        <section class="card stock-head">
          <div class="stock-title">
            <span class="stock-name">{{ current.name }}</span>
            <span class="stock-code num">{{ current.ts_code }}</span>
          </div>
          <div class="stock-tags muted">
            {{ current.industry }} · {{ current.area }} · 上市 {{ current.list_date }}
          </div>
          <button
            class="fav-btn" :class="{ active: watchlist.has(tsCode) }"
            :aria-pressed="watchlist.has(tsCode)"
            :title="watchlist.has(tsCode) ? '从自选股移除' : '加入自选股（保存在本机浏览器）'"
            @click="watchlist.toggle(tsCode)"
          >
            <Icon name="star" />
            {{ watchlist.has(tsCode) ? '已自选' : '加自选' }}
          </button>
        </section>

        <UserValuation :ts-code="tsCode" />

        <section v-if="watchItems.length" class="watch-bar" aria-label="自选股快速切换">
          <span class="watch-label"><Icon name="star" />自选股</span>
          <span
            v-for="w in watchItems" :key="w.ts_code"
            class="watch-chip" :class="{ active: w.ts_code === tsCode }"
          >
            <button class="chip-main" @click="tsCode = w.ts_code">
              {{ w.name }}<code class="num">{{ w.symbol }}</code>
            </button>
            <button class="chip-x" :aria-label="`从自选股移除 ${w.name}`" @click="watchlist.remove(w.ts_code)">
              <Icon name="x" />
            </button>
          </span>
        </section>
        <p v-else class="watch-hint muted">
          小提示：点股票信息卡右侧「加自选」，可把常看的股票收藏在本机浏览器，随时一键切换
        </p>

        <div class="tabs">
          <button
            v-for="t in TABS" :key="t.key"
            class="tab-btn" :class="{ active: activeTab === t.key }"
            @click="activeTab = t.key"
          >{{ t.label }}</button>
        </div>

        <component :is="activeComponent" :ts-code="tsCode" @pick="onPick" />
      </template>
    </main>

    <footer class="user-footer muted">
      本服务仅供学习与研究使用，所有分析与预测结果不构成投资建议 · 股市有风险，投资需谨慎
    </footer>
  </div>
</template>

<script setup>
/**
 * 用户端入口页（/#/user）：与调试端布局完全隔离（见 App.vue 的 isUserApp 分支）。
 * - 只提供精简核心功能：股票模糊搜索 + 综合概览 / 模型预测 / 风险提示（复用调试端分析组件）
 * - 不暴露数据模式切换（dataSource 仍由 localStorage / VITE_DATA_SOURCE 决定，演示模式顶部有角标提示）
 * - 股票池加载逻辑与 AnalysisView 相同：AbortController + generation guard 防过期响应
 */
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import Icon from '../components/Icon.vue'
import StockSearch from '../components/StockSearch.vue'
import AnalysisOverview from './analysis/AnalysisOverview.vue'
import AnalysisPrediction from './analysis/AnalysisPrediction.vue'
import AnalysisRisk from './analysis/AnalysisRisk.vue'
import UserTechnical from './user/UserTechnical.vue'
import UserStats from './user/UserStats.vue'
import UserCompare from './user/UserCompare.vue'
import UserMarket from './user/UserMarket.vue'
import UserValuation from './user/UserValuation.vue'
import { dataSource } from '../api/client.js'
import { getStockPool } from '../api/analysis.js'
import { useThemeStore } from '../stores/theme.js'
import { useWatchlistStore } from '../stores/watchlist.js'

const TABS = [
  { key: 'market', label: '市场概览', comp: UserMarket },
  { key: 'overview', label: '综合概览', comp: AnalysisOverview },
  { key: 'technical', label: '技术指标', comp: UserTechnical },
  { key: 'stats', label: '统计分析', comp: UserStats },
  { key: 'compare', label: '多股对比', comp: UserCompare },
  { key: 'prediction', label: '模型预测', comp: AnalysisPrediction },
  { key: 'risk', label: '风险提示', comp: AnalysisRisk },
]

const theme = useThemeStore()
const watchlist = useWatchlistStore()
const live = computed(() => dataSource.value === 'hybrid')

const pool = ref([])
const tsCode = ref('')
const loading = ref(false)
const error = ref('')
const activeTab = ref('overview')
const activeComponent = shallowRef(AnalysisOverview)
let controller
let generation = 0

const current = computed(() => pool.value.find((s) => s.ts_code === tsCode.value))

// 自选股 → 池内完整信息（不在当前池中的收藏自动隐藏，如 live/mock 池切换后）
const watchItems = computed(() =>
  watchlist.codes.map((c) => pool.value.find((s) => s.ts_code === c)).filter(Boolean))

// 市场概览排行榜点股 → 切换主选股并跳到综合概览（池外代码忽略，防止 current 为空）
function onPick(code) {
  if (!pool.value.some((s) => s.ts_code === code)) return
  tsCode.value = code
  activeTab.value = 'overview'
}

watch(activeTab, () => {
  activeComponent.value = TABS.find((t) => t.key === activeTab.value)?.comp || AnalysisOverview
}, { immediate: true })

async function loadPool() {
  controller?.abort()
  controller = new AbortController()
  const run = ++generation
  loading.value = true; error.value = ''; pool.value = []; tsCode.value = ''
  try {
    const result = await getStockPool({ signal: controller.signal })
    if (run !== generation) return
    pool.value = result
    tsCode.value = result.find(s => s.ts_code === '000001.SZ')?.ts_code || result[0]?.ts_code || ''
  } catch (e) { if (run === generation && e.name !== 'AbortError') error.value = e.message }
  finally { if (run === generation) loading.value = false }
}

onMounted(loadPool)
onBeforeUnmount(() => { generation++; controller?.abort() })
</script>

<style scoped>
.user-app {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--page);
}

/* ── 顶栏 ─────────────────────────────────────── */
.user-topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 56px;
  padding: 0 20px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.brand-mark {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 16px;
  color: #fff;
  background: var(--accent);
  flex: none;
}

.brand-text {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-1);
  line-height: 1.2;
  white-space: nowrap;
}

.brand-text small {
  display: block;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.demo-badge {
  font-size: 11.5px;
  padding: 3px 10px;
  border-radius: 999px;
  color: var(--warning);
  background: rgba(250, 178, 25, 0.12);
  border: 1px solid rgba(250, 178, 25, 0.35);
  white-space: nowrap;
}

.theme-btn {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-2);
  cursor: pointer;
}

.theme-btn:hover {
  color: var(--text-1);
  border-color: var(--border-strong);
}

.theme-btn svg {
  width: 16px;
  height: 16px;
}

/* ── 内容区 ───────────────────────────────────── */
.user-content {
  flex: 1;
  width: 100%;
  max-width: 1160px;
  margin: 0 auto;
  padding: 0 20px 28px;
  box-sizing: border-box;
}

.hero {
  text-align: center;
  padding: 34px 0 20px;
}

.hero h1 {
  font-size: 24px;
  font-weight: 800;
  color: var(--text-1);
  margin-bottom: 8px;
}

.hero-sub {
  font-size: 13.5px;
  margin-bottom: 18px;
}

.hero-search {
  max-width: 520px;
  margin: 0 auto;
  text-align: left;
}

.state-text {
  text-align: center;
  padding: 24px 0;
  font-size: 13.5px;
  color: var(--text-2);
}

/* ── 股票信息卡 ───────────────────────────────── */
.stock-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 16px 20px;
  margin-bottom: 14px;
}

.stock-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.stock-name {
  font-size: 19px;
  font-weight: 700;
  color: var(--text-1);
}

.stock-code {
  font-size: 13px;
  color: var(--text-3);
}

.stock-tags {
  font-size: 12.5px;
}

/* ── 收藏与自选股 ─────────────────────────────── */
.fav-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-2);
  font-size: 12.5px;
  cursor: pointer;
  white-space: nowrap;
}

.fav-btn:hover {
  color: var(--text-1);
  border-color: var(--border-strong);
}

.fav-btn svg {
  width: 14px;
  height: 14px;
}

.fav-btn.active {
  color: var(--warning);
  border-color: var(--warning);
  background: rgba(250, 178, 25, 0.1);
  font-weight: 650;
}

.watch-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.watch-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-3);
}

.watch-label svg {
  width: 13px;
  height: 13px;
  color: var(--warning);
}

.watch-chip {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  overflow: hidden;
}

.watch-chip.active {
  border-color: var(--accent);
}

.chip-main {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 5px 4px 5px 12px;
  background: transparent;
  border: none;
  color: var(--text-2);
  font-size: 12.5px;
  cursor: pointer;
}

.chip-main code {
  font-size: 10.5px;
  color: var(--text-3);
}

.watch-chip.active .chip-main {
  color: var(--accent);
  font-weight: 650;
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

.watch-hint {
  font-size: 11.5px;
  margin-bottom: 14px;
}

/* ── 页脚 ─────────────────────────────────────── */
.user-footer {
  padding: 16px 20px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  text-align: center;
  line-height: 1.7;
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

@media (max-width: 800px) {
  .hero { padding: 22px 0 14px; }
  .hero h1 { font-size: 20px; }
  .brand-text small { display: none; }
  .demo-badge { display: none; }
  .user-content { padding: 0 12px 20px; }
}
</style>
