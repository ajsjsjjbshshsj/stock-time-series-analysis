<template>
  <div>
    <!-- 加载 / 错误 / 空态 -->
    <div class="card section">
      <div class="card-title">Transformer 冻结信号 <span v-if="demo" class="demo-note">演示数据 · 非真实信号</span></div>
      <div class="card-sub">离线冻结模型的已封存信号包 · 本页只读呈现，绝不触发训练或推理</div>
      <p v-if="loading" role="status">信号包加载中…</p>
      <p v-else-if="error" role="alert">{{ error }} <button class="btn btn-sm" @click="load">重试</button></p>
      <p v-else-if="!signals" role="status" class="muted">
        暂无已发布信号包。信号由后台离线脚本产出并哈希封存后才会出现在这里；刷新信号需人工重跑约 1 小时的离线流程，本页只读、绝不触发。
      </p>
    </div>

    <template v-if="signals">
      <!-- ① 状态横幅：协议分类必须诚实呈现 -->
      <div class="card section banner" :class="signals.prospective_eligible ? 'banner-accent' : 'banner-warning'">
        <StatusTag :status="signals.status" />
        <div class="banner-text">
          <template v-if="signals.prospective_eligible">
            前瞻候选：发布于 {{ signals.published_at }}，早于开盘截止 {{ signals.deadline }}，可作为盘前候选参考。
          </template>
          <template v-else>
            迟到诊断：发布于 {{ signals.published_at }}，晚于开盘截止 {{ signals.deadline }} ——
            属<b>历史诊断信息，非前瞻候选信号</b>，不应用于指导当期交易。
          </template>
        </div>
      </div>

      <!-- ② 元信息 -->
      <div class="grid stat-grid section">
        <StatCard label="信号日" :value="signals.signal_date" raw :sub="`主排序口径：${signals.primary_policy}（预声明）`" />
        <StatCard label="持有窗口" :value="`${signals.entry_date} → ${signals.exit_date}`" raw sub="T+1 入场 · T+5 出场（协议固定）" />
        <StatCard label="模型冻结时间" :value="signals.frozen_at" raw sub="6 模型此后不再变动" />
        <StatCard label="数据截止" :value="signals.cutoff" raw sub="信号日晚于数据截止（协议要求）" />
        <StatCard label="覆盖范围" value="20 只试点" sub="深市试点票，非全市场" />
        <StatCard label="信号库" :value="signals.store" raw :sub="`可用信号日：${signals.available_dates.join('、')}`" />
      </div>
      <p v-if="expired" class="muted section note">
        本信号包持有窗口已于 {{ signals.exit_date }} 结束，属历史封存快照；刷新需重跑约 1 小时离线脚本，本页只读绝不触发。
      </p>
      <div class="card section">
        <details>
          <summary>防篡改完整性复核（哈希链）</summary>
          <p class="muted">服务前已重算 marker / receipt / seal / payload 哈希并交叉核对，全部一致（verified = {{ signals.integrity.verified }}）。</p>
          <p class="hash-line"><span class="muted">marker_sha256</span><code class="hash">{{ signals.integrity.marker_sha256 }}</code></p>
          <p class="hash-line"><span class="muted">receipt_sha256</span><code class="hash">{{ signals.integrity.receipt_sha256 }}</code></p>
          <p class="hash-line"><span class="muted">binding_sha256</span><code class="hash">{{ signals.integrity.binding_sha256 }}</code></p>
          <p class="hash-line"><span class="muted">payload_sha256</span><code class="hash">{{ signals.integrity.payload_sha256 }}</code></p>
        </details>
      </div>

      <!-- ③ 所选股面板 -->
      <div class="card section">
        <div class="card-title">所选股票 · {{ tsCode }}</div>
        <template v-if="selected">
          <div class="grid stat-grid">
            <StatCard label="主口径排名（raw）" :value="`${selected.rank_raw} / 20`" raw tone="accent" :sub="`共识均值 ${selected.mean_raw.toFixed(4)}（无量纲排序分，非收益率）`" />
            <StatCard label="次级口径排名（非负方差）" :value="`${selected.rank_nonnegative_variance} / 20`" raw :sub="`共识均值 ${selected.mean_nonnegative_variance.toFixed(4)}`" />
          </div>
          <div class="card-sub section">6 个冻结模型对该股的原始排序分（平等保留，无收益事后筛选）</div>
          <DataTable :columns="scoreColumns" :rows="selectedScores" row-key="id" />
        </template>
        <p v-else class="muted">
          该股票不在本期 20 只试点范围内（当前信号包仅覆盖冻结协议预声明的深市试点票），下方排行与 Top5 榜单照常可看。
        </p>
      </div>

      <!-- ④ 6 模型 Top5 -->
      <div class="card section">
        <div class="card-title">6 模型 Top5 榜单</div>
        <div class="card-sub">raw = 预声明主排序口径 · nonnegative_variance = 次级口径 · 6 模型平等保留，无收益事后筛选 · 指纹为 model_sha256 前 12 位</div>
        <DataTable :columns="modelColumns" :rows="modelRows" row-key="id" />
      </div>

      <!-- ⑤ 共识排行 + 条形图 -->
      <div class="grid two-col section">
        <div class="card">
          <div class="card-title">共识排行（20 只试点票）</div>
          <div class="card-sub">6 模型 raw 排序分的均值排名 · tie-break 按代码升序（协议固定） · 均值为展示层聚合，非协议级选择</div>
          <DataTable :columns="consensusColumns" :rows="consensusRows" row-key="ts_code">
            <template #cell-ts_code="{ row }">
              <span class="num" :class="{ 'ts-selected': row.ts_code === tsCode }">{{ row.ts_code }}<template v-if="row.ts_code === tsCode"> ●</template></span>
            </template>
          </DataTable>
        </div>
        <div class="card">
          <div class="card-title">raw 共识均值分布</div>
          <div class="card-sub">无量纲排序分（可为负），非收益率 · 高亮为所选股票</div>
          <ChartBox :option="consensusOption" :height="460" />
        </div>
      </div>

      <!-- ⑥ 免责 -->
      <div class="card section">
        <div class="card-sub">
          说明：本页全部数值为无量纲排序分，仅表达 6 个冻结模型的相对偏好顺序，不构成任何收益率承诺；
          信号包为哈希封存的历史快照，仅供学习与研究使用，不构成投资建议。
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
/**
 * Transformer 冻结信号（只读呈现层）。
 * - 数据来自 GET /api/analysis/transformer/signals：离线封存包（payload/marker/receipt/seal 哈希链），
 *   服务端每次响应前都重算 verify()；hybrid 下再经 normalizeTransformerSignals 严格校验。
 * - 信号包是全局的（20 只试点票整体），tsCode 只驱动"所选股面板"，不重新请求。
 * - 协议红线：raw 为预声明主排序口径；6 模型平等保留；分类（PROSPECTIVE_CANDIDATE /
 *   LATE_DIAGNOSTIC）由服务端按封存证据重算，前端诚实呈现，绝不改写。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ChartBox from '../../components/ChartBox.vue'
import DataTable from '../../components/DataTable.vue'
import StatCard from '../../components/StatCard.vue'
import StatusTag from '../../components/StatusTag.vue'
import { getTransformerSignals } from '../../api/analysis.js'
import { dataSource } from '../../api/client.js'
import { createGenerationGuard } from '../../utils/prediction.js'
import { baseOption, barSeries, chrome, seriesColors, xAxis, yAxis } from '../../utils/chart.js'
import { useThemeStore } from '../../stores/theme.js'

const props = defineProps({ tsCode: { type: String, required: true } })

const theme = useThemeStore()
const guard = createGenerationGuard()
const signals = ref(null)
const loading = ref(false)
const error = ref('')

const demo = computed(() => dataSource.value !== 'hybrid')
const selected = computed(() => signals.value?.consensus.find((r) => r.ts_code === props.tsCode))
const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' })
const expired = computed(() => Boolean(signals.value) && signals.value.exit_date <= today)

const MODE_LABELS = { adjusted: '复权', unadjusted_control: '未复权对照' }

async function load() {
  const run = guard.next()
  loading.value = true; error.value = ''; signals.value = null
  try {
    const dto = await getTransformerSignals('', { signal: run.signal })
    if (run.current()) signals.value = dto
  } catch (e) {
    if (run.current() && e.name !== 'AbortError') error.value = e.message
  } finally {
    if (run.current()) loading.value = false
  }
}

watch(dataSource, load, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => guard.cancel())

/* 所选股 6 模型分数明细 */
const scoreColumns = [
  { key: 'seed', label: 'Seed', type: 'number', format: String },
  { key: 'mode', label: '口径', format: (m) => MODE_LABELS[m] || m },
  { key: 'raw', label: 'raw 排序分（主）', type: 'number', format: (v) => v.toFixed(4) },
  { key: 'nonnegative_variance', label: '非负方差排序分（次）', type: 'number', format: (v) => v.toFixed(4) },
]
const selectedScores = computed(() => (selected.value?.scores || []).map((s) => ({ ...s, id: `${s.seed}:${s.mode}` })))

/* 6 模型 Top5 榜单 */
const modelColumns = [
  { key: 'seed', label: 'Seed', type: 'number', format: String },
  { key: 'mode', label: '口径', format: (m) => MODE_LABELS[m] || m },
  { key: 'sha', label: '指纹（前12位）' },
  { key: 'raw', label: 'Top5 · raw（主）', ellipsis: true },
  { key: 'nv', label: 'Top5 · 非负方差（次）', ellipsis: true },
]
const modelRows = computed(() => (signals.value?.models || []).map((m) => ({
  id: `${m.seed}:${m.mode}`,
  seed: m.seed,
  mode: m.mode,
  sha: m.model_sha256.slice(0, 12),
  raw: m.top5.raw.join('  '),
  nv: m.top5.nonnegative_variance.join('  '),
})))

/* 共识排行表 + 条形图 */
const consensusColumns = [
  { key: 'rank_raw', label: '主排名', type: 'number', width: '70px' },
  { key: 'ts_code', label: '股票', width: '110px' },
  { key: 'mean_raw', label: 'raw 共识均值', type: 'number', format: (v) => v.toFixed(4) },
  { key: 'rank_nonnegative_variance', label: '次排名', type: 'number', width: '70px' },
  { key: 'mean_nonnegative_variance', label: '非负方差均值', type: 'number', format: (v) => v.toFixed(4) },
]
const byRank = computed(() => [...(signals.value?.consensus || [])].sort((a, b) => a.rank_raw - b.rank_raw))
const consensusRows = byRank
const consensusOption = computed(() => {
  const c = chrome(theme.isDark)
  const accent = seriesColors(theme.isDark)[0]
  const rows = [...byRank.value].reverse() // 横向条形图自下而上 → 反转后 rank1 在顶部
  return {
    ...baseOption(theme.isDark),
    legend: { show: false },
    grid: { left: 8, right: 34, top: 8, bottom: 2, containLabel: true },
    xAxis: yAxis(theme.isDark),
    yAxis: xAxis(theme.isDark, { data: rows.map((r) => r.ts_code), axisLabel: { fontSize: 9.5 } }),
    series: [barSeries('raw 共识均值', rows.map((r) => ({
      value: r.mean_raw,
      itemStyle: {
        borderRadius: [0, 3, 3, 0],
        color: r.ts_code === props.tsCode ? accent : c.muted,
      },
    })))]
  }
})
</script>

<style scoped>
.demo-note {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--warning);
  background: rgba(250, 178, 25, 0.12);
  border: 1px solid rgba(250, 178, 25, 0.35);
  border-radius: 999px;
  padding: 1px 9px;
  vertical-align: middle;
}

.banner { display: flex; align-items: flex-start; gap: 12px; padding: 14px 18px; }
.banner-accent { border-left: 3px solid var(--accent); }
.banner-warning { border-left: 3px solid var(--warning); }
.banner-text { font-size: 13px; line-height: 1.7; color: var(--text-2); }

.stat-grid { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
.two-col { grid-template-columns: 1fr 1fr; }
.note { font-size: 12.5px; }

.hash-line { display: flex; flex-direction: column; gap: 2px; margin: 6px 0; font-size: 12px; }
.hash { font-family: 'Cascadia Code', Consolas, monospace; font-size: 11.5px; overflow-wrap: anywhere; color: var(--text-2); }

.ts-selected { color: var(--accent); font-weight: 700; }

details summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--text-1); }

@media (max-width: 1200px) { .two-col { grid-template-columns: 1fr; } }
</style>
