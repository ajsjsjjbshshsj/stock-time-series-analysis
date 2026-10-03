<template>
  <div>
    <!-- 股票选择 + Tab -->
    <div class="analysis-head">
      <div class="field stock-select">
        <label>股票（mock 股票池 · seeded 随机游走 260 交易日）</label>
        <select v-model="tsCode">
          <option v-for="s in pool" :key="s.ts_code" :value="s.ts_code">
            {{ s.ts_code }} · {{ s.name }} · {{ s.industry }}
          </option>
        </select>
      </div>
      <div v-if="current" class="stock-meta muted">
        {{ current.area }} · 上市 {{ current.list_date }}
      </div>
    </div>

    <div class="tabs">
      <button
        v-for="t in TABS" :key="t.key"
        class="tab-btn" :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >{{ t.label }}</button>
    </div>

    <component :is="activeComponent" :ts-code="tsCode" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { getStockPool } from '../api/analysis.js'
import AnalysisOverview from './analysis/AnalysisOverview.vue'
import AnalysisBasic from './analysis/AnalysisBasic.vue'
import AnalysisTechnical from './analysis/AnalysisTechnical.vue'
import AnalysisStats from './analysis/AnalysisStats.vue'
import AnalysisPrediction from './analysis/AnalysisPrediction.vue'
import AnalysisBacktest from './analysis/AnalysisBacktest.vue'
import AnalysisRanking from './analysis/AnalysisRanking.vue'
import AnalysisTransformer from './analysis/AnalysisTransformer.vue'
import AnalysisRisk from './analysis/AnalysisRisk.vue'

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
const tsCode = ref('000001.SZ')
const activeTab = ref('overview')
const activeComponent = shallowRef(AnalysisOverview)

const current = computed(() => pool.value.find((s) => s.ts_code === tsCode.value))

function syncComponent() {
  activeComponent.value = TABS.find((t) => t.key === activeTab.value)?.comp || AnalysisOverview
}

watch(activeTab, syncComponent, { immediate: true })

onMounted(async () => {
  pool.value = await getStockPool()
})
</script>

<style scoped>
.analysis-head {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.stock-select select {
  min-width: 280px;
}

.stock-meta {
  font-size: 12.5px;
  padding-bottom: 8px;
}
</style>
