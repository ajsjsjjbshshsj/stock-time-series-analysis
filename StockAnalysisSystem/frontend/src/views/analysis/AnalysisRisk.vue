<template>
  <div>
    <div class="card risk-card section">
      <div class="risk-icon">
        <Icon name="alert" />
      </div>
      <div class="risk-title">风险提示与免责声明</div>
      <ul class="risk-list">
        <li>本系统所有分析、预测、回测与排名结果<b>仅供学习与研究使用</b>，不构成任何投资建议。</li>
        <li>模型基于历史数据训练，历史表现<b>不代表未来收益</b>；股市有风险，投资需谨慎。</li>
        <li>Mock 演示数据由固定种子的随机游走生成，<b>与真实市场行情无关</b>，不指向任何真实证券的未来走势。</li>
        <li>模型评估指标（Accuracy / RMSE / Direction_Accuracy 等）均为样本内外验证的统计量，实际部署效果受数据质量、市场状态漂移等因素影响。</li>
        <li>任何依据本系统内容进行的投资决策，风险由决策者<b>自行承担</b>。</li>
      </ul>
    </div>

    <div class="card section">
      <div class="card-title">analysis_result 表预览（{{ tsCode }}）</div>
      <div class="card-sub">{{ live
        ? '冻结模型发布落库摘要（V0.7 XGBoost · 下一交易日收盘收益率口径）· 只读呈现'
        : '预测结果落库记录 · result 为 JSON 明细 · prediction / confidence 冗余存对应模型指标' }}</div>
      <p v-if="loading" role="status">发布记录加载中…</p>
      <p v-else-if="error" role="alert">{{ error }} <button class="btn btn-sm" @click="load(tsCode)">重试</button></p>
      <DataTable v-else :columns="resultColumns" :rows="results" :row-key="rowKey" />
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import DataTable from '../../components/DataTable.vue'
import Icon from '../../components/Icon.vue'
import { getAnalysisResults } from '../../api/analysis.js'
import { dataSource } from '../../api/client.js'
import { fmtNum } from '../../utils/format.js'
import { createGenerationGuard, returnPercent } from '../../utils/prediction.js'

const props = defineProps({ tsCode: { type: String, required: true } })
const results = ref([])
const loading = ref(false)
const error = ref('')
const guard = createGenerationGuard()

// hybrid 下 getAnalysisResults 返回的是发布摘要行（publication_id/model_id/…），
// 与 mock 的 analysis_result 原始列（id/analysis_date/…）完全不同构，列必须切换。
const live = computed(() => dataSource.value === 'hybrid')

async function load(code) {
  const run = guard.next()
  loading.value = true; error.value = ''; results.value = []
  try {
    const rows = await getAnalysisResults(code, { signal: run.signal })
    if (run.current()) results.value = rows
  } catch (e) {
    if (run.current() && e.name !== 'AbortError') error.value = e.message
  } finally {
    if (run.current()) loading.value = false
  }
}

watch(() => [props.tsCode, dataSource.value], () => load(props.tsCode), { immediate: true })
onBeforeUnmount(() => guard.cancel())

const mockColumns = [
  { key: 'id', label: 'id', width: '60px', align: 'right', type: 'number', format: (v) => String(v) },
  { key: 'ts_code', label: 'ts_code', width: '96px' },
  { key: 'analysis_date', label: 'analysis_date', width: '104px' },
  { key: 'analysis_type', label: 'analysis_type', width: '170px' },
  { key: 'result', label: 'result(JSON)', ellipsis: true },
  { key: 'prediction', label: 'prediction', width: '96px', align: 'right', type: 'number', format: (v) => fmtNum(v, 4) },
  { key: 'confidence', label: 'confidence', width: '96px', align: 'right', type: 'number', format: (v) => fmtNum(v, 4) },
  { key: 'created_at', label: 'created_at', width: '150px' },
]
const percent = (value) => value === null || value === undefined ? '暂无' : `${returnPercent(value).toFixed(4)}%`
const liveColumns = [
  { key: 'publication_id', label: '发布 ID', width: '76px', align: 'right', type: 'number', format: String },
  { key: 'model_id', label: 'model_id', ellipsis: true },
  { key: 'ts_code', label: '股票', width: '96px' },
  { key: 'data_cutoff', label: '行情截止', width: '100px' },
  { key: 'target', label: 'target', width: '210px', ellipsis: true },
  { key: 'horizon', label: 'horizon', width: '74px', align: 'right', type: 'number', format: String },
  { key: 'latest', label: '最新信号预测', width: '110px', align: 'right', format: (v) => v ? percent(v.predicted_return) : '暂无' },
]
const resultColumns = computed(() => (live.value ? liveColumns : mockColumns))
const rowKey = computed(() => (live.value ? 'publication_id' : 'id'))
</script>

<style scoped>
.risk-card {
  border-color: rgba(250, 178, 25, 0.4);
  background: linear-gradient(180deg, rgba(250, 178, 25, 0.05), transparent 120px);
  padding: 24px 28px;
}

.risk-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  color: var(--warning);
  background: rgba(250, 178, 25, 0.12);
  margin-bottom: 12px;
}

.risk-icon svg {
  width: 24px;
  height: 24px;
}

.risk-title {
  font-size: 17px;
  font-weight: 700;
  margin-bottom: 10px;
}

.risk-list {
  margin-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.risk-list li {
  font-size: 13px;
  color: var(--text-2);
  line-height: 1.65;
}

.risk-list b {
  color: var(--text-1);
}
</style>
