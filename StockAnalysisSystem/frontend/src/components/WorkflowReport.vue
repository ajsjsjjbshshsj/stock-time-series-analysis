<template>
  <section class="report" v-if="value">
    <h3>{{ value.title }} · {{ value.algorithm }}</h3>
    <p class="muted">结果 {{ value.id }}<br />模型 {{ value.model_id }} · {{ value.report.status === 'COMPLETE' ? '结果完整' : '未就绪' }}</p>
    <dl><dt>信号范围</dt><dd>{{ value.report.requested_start || value.report.start_date || '见报告' }} → {{ value.report.requested_end || value.report.end_date || '见报告' }}</dd>
      <dt>实际信号截止</dt><dd>{{ displayContext.signalEnd }}</dd>
      <dt>数据源截止</dt><dd>{{ displayContext.dataCutoff }}</dd><dt>模型已见数据</dt><dd>{{ value.report.seen_through || '见模型记录' }}</dd>
      <dt>股票池</dt><dd>{{ displayContext.poolLabel }}<template v-if="displayContext.stockCodes"><br />{{ displayContext.stockCodes }}</template></dd>
      <template v-if="displayContext.poolVersion"><dt>固定池版本</dt><dd><code>{{ displayContext.poolVersion }}</code></dd>
        <dt>股票池来源</dt><dd>{{ displayContext.poolGroup }} · {{ displayContext.poolProvider }} · {{ displayContext.poolSnapshotDate }}<template v-if="displayContext.registryStockCount != null"> · 归属池 {{ displayContext.registryStockCount }}只</template></dd></template>
      <dt v-if="value.report.scored_stocks != null">实际参与评分</dt><dd v-if="value.report.scored_stocks != null">{{ value.report.scored_stocks }} 只；可能因逐日资格与固定池数量不同</dd>
      <dt>分数量纲</dt><dd>{{ scoreLabel(value.report.score_units) }}</dd><dt>持有周期</dt><dd>{{ value.report.holding_sessions }} 交易日 · 标签偏移 {{ value.report.horizon }}</dd>
      <dt>样本属性</dt><dd>{{ value.report.sample_relation === 'post_model_selection' ? '模型选定后的样本' : '历史研究结果（不等于独立样本外验证）' }}</dd></dl>
    <p>仅供研究；收益不保证为正，不同量纲模型不能直接比较分数。</p>
    <p class="muted">{{ displayContext.provenance }}</p>
    <p v-if="value.report.status !== 'COMPLETE'" role="alert">执行证据不足，本结果不能发布；请查看缺失证据分区，不补造价格或收益。</p>
    <dl v-if="value.report.metrics"><template v-for="(v,k) in value.report.metrics" :key="k"><dt>{{ k }}</dt><dd>{{ v == null ? '未定义' : v }}</dd></template></dl>
    <ChartBox v-if="curve" :option="curve" :height="280" />
    <p v-if="value.section === 'equity'" class="muted">曲线仅展示当前服务端分页，不代表完整区间。预测结果不生成虚拟收益曲线。</p>
    <DataTable :columns="columns" :rows="value.rows" empty-text="当前分区无记录；可能不适用或所选股票不在此结果中" />
    <details><summary>费用、执行假设与完整报告</summary><pre>{{ JSON.stringify(value.report, null, 2) }}</pre></details>
    <details><summary>完整性 SHA-256</summary><code>{{ value.manifest_sha256 }}</code></details>
  </section>
</template>
<script setup>
import { computed } from 'vue'
import DataTable from './DataTable.vue'
import ChartBox from './ChartBox.vue'
import { scoreLabel, resultDisplayContext } from '../utils/workflows.js'
const props = defineProps({ value: { type: Object, default: null } })
const displayContext = computed(() => resultDisplayContext(props.value || {}))
const columns = computed(() => [...new Set((props.value?.rows || []).flatMap(Object.keys))].map(key => ({ key, label: key })))
const curve = computed(() => props.value?.section === 'equity' && props.value.rows.some(r => Number.isFinite(r.equity)) ? {
  tooltip: { trigger: 'axis' }, grid: { left: 75, right: 20, bottom: 40 },
  xAxis: { type: 'category', data: props.value.rows.map(r => r.date) }, yAxis: { type: 'value', scale: true },
  series: [{ type: 'line', showSymbol: false, data: props.value.rows.map(r => r.equity) }],
} : null)
</script>
<style scoped>
.report { display:grid; gap:14px; min-width:0; }dl { display:grid; grid-template-columns:140px minmax(0,1fr); gap:8px; }dt,.muted { color:var(--text-3); }dd,code { overflow-wrap:anywhere; }pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:360px; overflow:auto; font-size:12px; }details { padding:10px 0; border-top:1px solid var(--border); }summary { cursor:pointer; }[role=alert] { color:var(--serious); }@media(max-width:640px){dl{grid-template-columns:110px minmax(0,1fr)}}
</style>
