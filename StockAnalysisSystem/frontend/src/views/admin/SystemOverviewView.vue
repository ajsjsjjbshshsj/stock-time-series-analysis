<template>
  <div class="overview">
    <section class="card box">
      <header><div><h1>系统总览</h1><p class="muted">真实数据与运行证据 · 只读汇总，不触发采集、训练、预测或发布</p></div><button class="btn" :disabled="!enabled || loading" @click="control.refresh">{{ loading ? '刷新中…' : '刷新总览' }}</button></header>
      <p v-if="!enabled" role="alert">请使用已登录的管理员账号查看系统总览。</p>
      <p class="muted">各模块独立读取，时间不一定相同。请求成功不等于数据已更新至今天；服务未返回的数据不会用演示值替代。</p>
      <nav class="actions" aria-label="业务快捷入口"><RouterLink v-for="link in links" :key="link[0]" class="btn btn-sm" :to="link[0]">{{ link[1] }}</RouterLink></nav>
    </section>

    <div v-if="enabled" class="panels">
      <section v-for="panel in panels" :key="panel.key" class="card box" :aria-busy="state[panel.key].loading">
        <header><h2>{{ panel.title }}</h2><RouterLink :to="panel.route">{{ panel.link }}</RouterLink></header>
        <p v-if="state[panel.key].loading" role="status">正在读取真实接口…</p>
        <p v-else-if="state[panel.key].error" role="alert" class="error">读取失败：{{ state[panel.key].error }}。未确认当前状态，请刷新重试。</p>
        <template v-else>
          <template v-if="panel.key === 'market'">
            <template v-if="state.market.data">
              <p>行情最新交易日：<strong>{{ state.market.data.trade_date }}</strong></p>
              <dl><dt>该日行情记录数</dt><dd>{{ state.market.data.total }}</dd><dt>上涨 / 下跌 / 平盘</dt><dd>{{ state.market.data.up_count }} / {{ state.market.data.down_count }} / {{ state.market.data.flat_count }}</dd></dl>
              <p class="muted">按数据库该日实际行情统计，不等于模型股票池数量；不代表缺口全部补齐。</p>
            </template><p v-else>数据库尚无行情快照。</p>
          </template>
          <template v-else-if="panel.key === 'collection' && state.collection.data">
            <dl><dt>等待 / 正在采集</dt><dd>{{ state.collection.data.tasks.QUEUED ?? 0 }} / {{ state.collection.data.tasks.RUNNING ?? 0 }}</dd><dt>已完成交易日任务</dt><dd>{{ state.collection.data.tasks.SUCCEEDED ?? 0 }}</dd><dt>部分完成 / 失败</dt><dd>{{ state.collection.data.tasks.PARTIAL ?? 0 }} / {{ state.collection.data.tasks.FAILED ?? 0 }}</dd></dl>
            <p class="muted">缺口补采任务按交易日计数，任务成功不等于股票每日都有行情；停牌不补造价格。</p>
          </template>
          <template v-else-if="panel.key === 'runtime' && state.runtime.data">
            <dl><dt>采集进程</dt><dd>{{ workerLabels[state.runtime.data.worker] }}</dd><dt>夜间计划（上海时间）</dt><dd>{{ state.runtime.data.scheduler.run_at }}</dd><dt>允许提交截止日期</dt><dd>{{ state.runtime.data.completed_date }}</dd></dl>
            <p v-if="state.runtime.data.scheduler.blocked_error" role="alert" class="error">夜间调度受阻：{{ state.runtime.data.scheduler.blocked_error }}</p>
            <p v-if="state.runtime.data.scheduler.retry_not_before">下次允许重试：{{ state.runtime.data.scheduler.retry_not_before }}</p>
            <p>最后调度扫描：{{ state.runtime.data.scheduler.last_scan_at || '尚无记录' }}</p>
            <p class="muted">进程存活不证明已启用夜间参数；计划时间不代表今夜任务已完成。</p>
          </template>
          <template v-else-if="panel.key === 'consumer' && state.consumer.data">
            <dl><dt>累计消费</dt><dd>{{ state.consumer.data.totalConsumed }}</dd><dt>成功 / 失败</dt><dd>{{ state.consumer.data.successCount }} / {{ state.consumer.data.failureCount }}</dd><dt>死信发布失败</dt><dd>{{ state.consumer.data.deadLetterPublishFailureCount }}</dd></dl>
            <p class="muted">Consumer 当前进程累计值，重启可归零；不是数据库总行数，也不是 Flink 消费进度。</p>
          </template>
          <template v-else-if="panel.key === 'health' && state.health.data">
            <p>Consumer 健康检查：<strong>{{ state.health.data.status }}</strong></p>
            <p class="muted">只代表该服务健康端点的响应，不能据此判断 Kafka、ClickHouse 或全部 worker 都正常。</p>
          </template>
          <template v-else-if="panel.key === 'indicators' && state.indicators.data">
            <p>样本股票：000001.SZ · {{ state.indicators.data.indicatorReady ? '已验证快照可读' : '未就绪 / 未验证' }}</p>
            <dl><dt>状态</dt><dd>{{ state.indicators.data.state }}</dd><dt>验证截止</dt><dd>{{ state.indicators.data.verifiedThrough || '—' }}</dd><dt>该股最近观察日</dt><dd>{{ state.indicators.data.latestObservation || '—' }}</dd></dl>
            <p class="muted">仅为这只股票的指标证据，不代表全市场覆盖，也不保证一直最新。</p>
          </template>
          <template v-else-if="['models', 'results', 'jobs'].includes(panel.key) && state[panel.key].data">
            <p class="muted">最后5条最近记录，非全库总数。{{ state[panel.key].data.next_cursor !== null ? '还有更早记录，请进入管理页面查看。' : '' }}</p>
            <p v-if="!state[panel.key].data.items.length">暂无记录。</p>
            <ol v-else class="records"><li v-for="row in state[panel.key].data.items" :key="row.id">
              <template v-if="panel.key === 'jobs'"><strong>{{ operationLabels[row.request.operation] || row.request.kind || '工程任务' }} · {{ row.request.algorithm || '' }}</strong><p>{{ stateLabels[row.state] || row.state }}</p></template>
              <template v-else><strong>{{ row.algorithm }} · {{ row.title }}</strong>
                <p v-if="panel.key === 'models'">训练截止：{{ row.metadata.train_end || '未提供' }} · 固定池 {{ row.metadata.pool_count ?? '未提供' }} 只</p>
                <p v-else>{{ row.published ? '已发布' : '未发布' }} · {{ row.metadata.status || '未提供状态' }} · {{ row.metadata.operation === 'backtest' ? '回测信号区间' : '预测信号日期' }} {{ resultSignalRange(row.metadata) }}</p>
              </template>
              <code>{{ row.id }}</code>
            </li></ol>
            <p class="muted" v-if="panel.key === 'results'">训练日期、行情日期和预测信号日期分别展示；有结果不代表已发布或保证盈利。</p>
          </template>
        </template>
        <footer v-if="state[panel.key].observedAt">本模块读取于 {{ localTime(state[panel.key].observedAt) }}（上海时间）</footer>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, watch, onBeforeUnmount } from 'vue'
import { RouterLink } from 'vue-router'
import { authState } from '../../stores/auth.js'
import { overviewApi } from '../../api/overview.js'
import { createOverviewState, createOverviewController, resultSignalRange } from '../../utils/overview.js'
import { stateLabels } from '../../utils/trainingTasks.js'
import { operationLabels } from '../../utils/workflows.js'

const enabled = computed(() => authState.mode === 'protected' && authState.user?.role === 'admin' && !authState.user.must_change_password)
const state = reactive(createOverviewState())
const control = createOverviewController(overviewApi, state, { enabled: () => enabled.value })
const loading = computed(() => Object.values(state).some(panel => panel.loading))
const workerLabels = { RUNNING: '进程存活（非完成证明）', STOPPED: '未启动', STALE_LOCK: '进程已退出，锁待核查', UNKNOWN: '未知' }
const links = [['/collector','数据采集'],['/monitor','消费监控'],['/pools','股票池管理'],['/training','训练任务'],['/models','模型工作台'],['/accounts','账户管理'],['/user','查看用户端']]
const panels = [
  {key:'market',title:'行情快照',route:'/analysis',link:'查看行情'},
  {key:'collection',title:'缺口补采',route:'/collector',link:'采集管理'},
  {key:'runtime',title:'采集进程与夜间计划',route:'/collector',link:'查看调度'},
  {key:'consumer',title:'Kafka 原始行情消费',route:'/monitor',link:'消费详情'},
  {key:'health',title:'Consumer 服务健康',route:'/monitor',link:'监控详情'},
  {key:'indicators',title:'指标验证样本',route:'/user',link:'指标图表'},
  {key:'jobs',title:'最近后台任务',route:'/models',link:'任务详情'},
  {key:'models',title:'最近保存模型',route:'/models',link:'模型管理'},
  {key:'results',title:'最近预测与回测结果',route:'/models',link:'结果管理'},
]
const localTime = stamp => new Date(stamp).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
// An identity change invalidates every pending response; never retain another administrator's observations.
watch(() => [authState.mode, authState.user?.id, authState.user?.role, authState.user?.must_change_password], () => control.refresh(), { immediate: true })
onBeforeUnmount(() => control.dispose())
</script>

<style scoped>
.overview { display: grid; gap: 18px; }
.box { padding: 20px; min-width: 0; }
header { display: flex; justify-content: space-between; align-items: start; gap: 12px; flex-wrap: wrap; }
h1, h2 { margin: 0 0 12px; }
h2 { font-size: 17px; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 350px), 1fr)); gap: 18px; align-items: start; }
dl { display: grid; grid-template-columns: 1fr auto; gap: 10px; }
dt { color: var(--text-3); } dd { margin: 0; font-variant-numeric: tabular-nums; }
p { line-height: 1.6; overflow-wrap: anywhere; }
.error { color: var(--warning); }
.records { padding-left: 22px; } .records li { padding: 10px 0; border-bottom: 1px solid var(--border); }
.records p { margin: 6px 0; } code { font-size: 11px; overflow-wrap: anywhere; }
footer { margin-top: 16px; font-size: 11px; color: var(--text-3); border-top: 1px solid var(--border); padding-top: 10px; }
</style>
