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

/* ── 5. 用户端功能丰富：本地计算新页签 + 自选股 + 多股对比 ── */
test('user app gains local-compute tabs and local-only watchlist', async () => {
  const view = await read('../src/views/UserAppView.vue')
  // 三个新用户端组件按 tab 挂载
  for (const comp of ['UserTechnical', 'UserStats', 'UserCompare']) {
    assert.match(view, new RegExp(`import ${comp} from '\\./user/${comp}\\.vue'`))
  }
  for (const label of ['技术指标', '统计分析', '多股对比']) assert.match(view, new RegExp(label))
  // 自选股：本地 store + 星标按钮 + 快速切换条；仍不暴露数据模式切换
  assert.match(view, /useWatchlistStore/)
  assert.match(view, /watchlist\.toggle\(tsCode\)/)
  assert.match(view, /fav-btn/)
  assert.match(view, /watch-chip/)
  assert.match(view, /name="star"/)
  assert.doesNotMatch(view, /setDataSource/)

  const store = await read('../src/stores/watchlist.js')
  assert.match(store, /sas-watchlist/) // 本地存储键
  assert.match(store, /STOCK_CODE/) // 代码校验
  assert.match(store, /MAX_ITEMS/) // 收藏上限
  assert.match(store, /function toggle/)

  const icon = await read('../src/components/Icon.vue')
  assert.match(icon, /name === 'star'/)
})

test('user technical and stats panels compute locally from kline', async () => {
  const tech = await read('../src/views/user/UserTechnical.vue')
  // 数据流：getKline → computeIndicators 本地计算，不依赖 8083 indicators（ClickHouse 未部署时 503）
  assert.match(tech, /getKline/)
  assert.match(tech, /computeIndicators/)
  assert.doesNotMatch(tech, /getIndicatorSeries/)
  assert.match(tech, /createGenerationGuard/)
  assert.match(tech, /settleDetailRequest/)
  for (const p of ['MACD', 'RSI', 'KDJ', 'BOLL', '量能']) assert.match(tech, new RegExp(p))
  assert.match(tech, /不构成投资建议/)

  const stats = await read('../src/views/user/UserStats.vue')
  assert.match(stats, /getKline/)
  assert.match(stats, /computeIndicators/)
  assert.match(stats, /computeStatistics/)
  // 不走 API 层的 getStatisticalAnalysis：hybrid 下该路径不在白名单，会静默落到演示数据
  assert.doesNotMatch(stats, /getStatisticalAnalysis/)
  assert.match(stats, /createGenerationGuard/)
})

test('user compare aligns common window and caps at four stocks', async () => {
  const cmp = await read('../src/views/user/UserCompare.vue')
  assert.match(cmp, /const MAX = 4/)
  assert.match(cmp, /getKline/)
  assert.match(cmp, /computeStatistics/)
  assert.match(cmp, /commonDates/) // 共同交易日交集对齐
  assert.match(cmp, /<StockSearch/)
  assert.match(cmp, /getStockPoolCached/) // 模块级池缓存（api 层共享），live 大池只拉一次
})
