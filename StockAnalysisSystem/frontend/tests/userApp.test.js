import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const read = (p) => readFile(new URL(p, import.meta.url), 'utf8')

/* ── 1. 路由：/user 入口存在且不污染调试端导航 ─────────── */
test('router exposes /user entry without polluting debug nav', async () => {
  const router = await read('../src/router/index.js')
  assert.match(router, /path: '\/user'/)
  assert.match(router, /UserAppView\.vue/)
  // 用户端路由用 meta.userApp 标记布局隔离，且绝不带 title（侧边栏按 meta.title 过滤生成）
  assert.match(router, /meta: \{ userApp: true \}/)
  const userRoute = router.slice(router.indexOf("path: '/user'"))
  assert.doesNotMatch(userRoute.slice(0, userRoute.indexOf('},')), /title:/)
  // 调试端 4 条路由原样保留
  for (const p of ['/overview', '/collector', '/monitor', '/analysis']) {
    assert.ok(router.includes(`path: '${p}'`), `调试端路由 ${p} 应保留`)
  }
})

/* ── 2. App.vue：布局按 userApp 隔离 ──────────────────── */
test('App.vue splits user app from debug layout', async () => {
  const app = await read('../src/App.vue')
  assert.match(app, /isUserApp/)
  assert.match(app, /route\.meta\?\.userApp/)
  // 用户端分支：裸 RouterView；调试布局在 v-else 分支原样保留
  assert.match(app, /<RouterView v-if="isUserApp"/)
  assert.match(app, /v-else class="layout"/)
  // 调试端资产不受影响：侧边栏导航、数据模式切换、时钟
  assert.match(app, /navRoutes/)
  assert.match(app, /source-select/)
  assert.match(app, /setDataSource/)
})

/* ── 3. UserAppView：复用搜索与三大核心分析组件 ────────── */
test('UserAppView reuses search and the three core analysis components', async () => {
  const view = await read('../src/views/UserAppView.vue')
  // 搜索框复用（含 label 关联与禁用联动）
  assert.match(view, /<StockSearch/)
  assert.match(view, /v-model="tsCode"/)
  assert.match(view, /:stocks="pool"/)
  assert.match(view, /:disabled="loading \|\| !pool\.length"/)
  assert.match(view, /for="user-stock-search"/)
  // 三个核心组件按 tab 复用，仍以 ts-code 驱动
  for (const comp of ['AnalysisOverview', 'AnalysisPrediction', 'AnalysisRisk']) {
    assert.match(view, new RegExp(`import ${comp} from '\\./analysis/${comp}\\.vue'`))
  }
  assert.match(view, /:ts-code="tsCode"/)
  for (const label of ['综合概览', '模型预测', '风险提示']) assert.match(view, new RegExp(label))
  // 默认选中 000001.SZ + generation guard 防过期响应
  assert.match(view, /000001\.SZ/)
  assert.match(view, /generation/)
  assert.match(view, /AbortController/)
  // 用户端不暴露数据模式切换，但演示模式要有角标提示 + 免责声明
  assert.doesNotMatch(view, /setDataSource/)
  assert.match(view, /demo-badge/)
  assert.match(view, /演示数据/)
  assert.match(view, /不构成投资建议/)
  // 主题切换保留（沿用 theme store）
  assert.match(view, /theme\.toggle/)
})

/* ── 4. 调试端 AnalysisView 未被改动破坏 ──────────────── */
test('debug AnalysisView keeps its nine tabs and search integration', async () => {
  const view = await read('../src/views/AnalysisView.vue')
  assert.match(view, /<StockSearch/)
  for (const comp of ['AnalysisOverview', 'AnalysisBasic', 'AnalysisTechnical', 'AnalysisStats',
    'AnalysisPrediction', 'AnalysisBacktest', 'AnalysisRanking', 'AnalysisTransformer', 'AnalysisRisk']) {
    assert.match(view, new RegExp(comp))
  }
})

/* ── 5. 用户端第 4 tab：Transformer 冻结信号（只读呈现） ── */
test('UserAppView adds the read-only transformer signals tab', async () => {
  const view = await read('../src/views/UserAppView.vue')
  assert.match(view, /import AnalysisTransformerSignals from '\.\/analysis\/AnalysisTransformerSignals\.vue'/)
  assert.match(view, /key: 'transformer', label: 'Transformer 信号', comp: AnalysisTransformerSignals/)
  // 原三个 tab 与后台端 9 tab 断言均不受影响（见上方两个测试）
  const signals = await read('../src/views/analysis/AnalysisTransformerSignals.vue')
  // 红线：只读呈现层绝不引用训练/触发计算端点
  assert.match(signals, /getTransformerSignals/)
  assert.doesNotMatch(signals, /getTransformerTraining|getTransformerPredictions|transformer\/train|transformer\/predict/)
  // 协议诚实呈现：排序分≠收益率、迟到诊断警示、哈希链展示
  assert.match(signals, /非收益率/)
  assert.match(signals, /迟到诊断/)
  assert.match(signals, /integrity/)
})
