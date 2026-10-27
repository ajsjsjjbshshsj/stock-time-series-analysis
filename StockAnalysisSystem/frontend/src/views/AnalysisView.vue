<template>
  <div>
    <!-- 股票选择 + Tab -->
    <div class="analysis-head">
      <div class="field stock-select">
        <label for="stock-code">{{ live ? '股票（真实 stock_basic 股票池）' : '股票（mock 股票池 · seeded 随机游走 260 交易日）' }}</label>
        <StockSearch id="stock-code" v-model="tsCode" :stocks="pool" :disabled="loading || !pool.length" />
      </div>
      <div v-if="current" class="stock-meta muted">
        {{ current.area }} · 上市 {{ current.list_date }}
      </div>
    </div>
    <p v-if="loading" role="status">股票池加载中…</p>
    <p v-else-if="error" role="alert">{{ error }} <button @click="loadPool">重试股票池</button></p>
    <p v-else-if="!pool.length" role="status">股票池为空，暂无可查询股票。</p>
    <p v-if="live" class="muted">真实只读分析：行情来自后端；技术与统计基于已加载日线在浏览器计算；排名、回测和 Transformer 仅展示已发布或历史封存结果。缺失与故障不会使用演示数据替代。</p>
    <p v-else class="muted">演示模式：种子模拟数据，不是真实行情或模型结果。</p>
    <nav v-if="live && authState.user?.role === 'admin'" class="workflow-links" aria-label="后台模型操作"><RouterLink to="/pools">股票池与数据准备</RouterLink><RouterLink to="/models">模型工作台</RouterLink><RouterLink to="/training">后台任务</RouterLink></nav>

    <div class="tabs">
      <button
        v-for="t in TABS" :key="t.key"
        class="tab-btn" :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >{{ t.label }}</button>
    </div>

    <component v-if="tsCode && !loading && !error" :is="activeComponent" :key="`${live}:${activeTab}`" :ts-code="tsCode" v-bind="liveProps" />
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { authState } from '../stores/auth.js'
import { dataSource } from '../api/client.js'
import { getStockPool } from '../api/analysis.js'
import StockSearch from '../components/StockSearch.vue'
import AnalysisOverview from './analysis/AnalysisOverview.vue'
import AnalysisBasic from './analysis/AnalysisBasic.vue'
import AnalysisTechnical from './analysis/AnalysisTechnical.vue'
import AnalysisStats from './analysis/AnalysisStats.vue'
import AnalysisPrediction from './analysis/AnalysisPrediction.vue'
import AnalysisBacktest from './analysis/AnalysisBacktest.vue'
import AnalysisRanking from './analysis/AnalysisRanking.vue'
import AnalysisTransformer from './analysis/AnalysisTransformer.vue'
import AnalysisRisk from './analysis/AnalysisRisk.vue'
import UserTechnical from './user/UserTechnical.vue'
import UserStats from './user/UserStats.vue'
import PublishedResults from './analysis/PublishedResults.vue'
import AnalysisTransformerLive from './analysis/AnalysisTransformerLive.vue'

const TABS = [
  { key: 'overview', label: '综合概览', comp: AnalysisOverview },
  { key: 'basic', label: '基本行情', comp: AnalysisBasic },
  { key: 'technical', label: '技术指标', comp: AnalysisTechnical },
  { key: 'stats', label: '统计分析', comp: AnalysisStats },
  { key: 'prediction', label: '模型预测', comp: AnalysisPrediction },
  { key: 'backtest', label: '策略回测', comp: AnalysisBacktest },
  { key: 'ranking', label: '选股排名', comp: AnalysisRanking },
  { key: 'transformer', label: 'Transformer', comp: AnalysisTransformer },
  { key: 'risk', label: '风险提示', comp: AnalysisRisk },
]

const pool = ref([])
const tsCode = ref('')
const live = computed(() => dataSource.value === 'hybrid')
const loading = ref(false)
const error = ref('')
let controller
let generation = 0
const activeTab = ref('overview')
const liveComponents = { basic: AnalysisBasic, technical: UserTechnical, stats: UserStats, backtest: PublishedResults, ranking: PublishedResults, transformer: AnalysisTransformerLive }
const activeComponent = computed(() => (live.value && liveComponents[activeTab.value]) || TABS.find(t => t.key === activeTab.value)?.comp || AnalysisOverview)
const liveProps = computed(() => !live.value ? {} : activeTab.value === 'backtest' ? { operation: 'backtest', initialSection: 'equity' } : activeTab.value === 'ranking' ? { operation: 'predict' } : {})

const current = computed(() => pool.value.find((s) => s.ts_code === tsCode.value))

watch(dataSource, loadPool)

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
.analysis-head {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.workflow-links { display:flex; flex-wrap:wrap; gap:16px; margin:16px 0; }

.stock-select {
  min-width: 320px;
  flex: 1 1 320px;
  max-width: 460px;
}

.stock-meta {
  font-size: 12.5px;
  padding-bottom: 8px;
}
</style>
