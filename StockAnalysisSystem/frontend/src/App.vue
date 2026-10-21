<template>
  <!-- 用户端（/user）：独立全屏布局，不带调试侧边栏/顶栏 -->
  <RouterView v-if="isUserApp" :key="dataSource + route.path" />

  <div v-else class="layout">
    <aside class="sidebar">
      <div class="logo">
        <div class="logo-mark">S</div>
        <div class="logo-text">
          StockAnalysisSystem
          <small>股票时序分析系统</small>
        </div>
      </div>

      <nav>
        <RouterLink
          v-for="r in navRoutes" :key="r.path"
          class="nav-item" :to="r.path"
        >
          <Icon :name="r.meta.icon" />
          {{ r.meta.title }}
        </RouterLink>
      </nav>

      <div class="sidebar-footer">
        python-collector · java kafka-consumer<br />
        stock-analysis-app · Flink · v0.8
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="topbar-title">{{ route.meta.title }}</div>
        <div class="topbar-right">
          <span class="mock-badge">{{ sourceLabel }}</span>
          <label class="source-select">
            <span class="sr-only">数据模式</span>
            <select :value="dataSource" :disabled="authState.mode === 'protected'" @change="setDataSource($event.target.value)" aria-label="数据模式">
              <option value="mock">演示模式</option>
              <option value="hybrid">接入现有 API</option>
            </select>
          </label>
          <button v-if="authState.mode === 'protected'" class="btn btn-sm" @click="logout">退出登录</button>
          <span v-if="logoutError" role="alert">{{ logoutError }}</span>
          <span class="clock num">{{ clock }}</span>
          <button
            class="theme-toggle"
            :title="theme.isDark ? '切换到浅色主题' : '切换到深色主题'"
            @click="theme.toggle()"
          >
            <Icon :name="theme.isDark ? 'sun' : 'moon'" />
          </button>
        </div>
      </header>

      <main class="content">
        <section v-if="authState.mode === 'protected' && ['overview', 'collector'].includes(route.name)" class="card">
          <h1>后台功能尚未接入</h1>
          <p>当前可使用账户管理、训练任务、消费监控和用户端真实结果。采集管理尚未接线，不展示模拟任务成功。</p>
          <RouterLink class="btn" to="/accounts">账户管理</RouterLink>
          <RouterLink class="btn" to="/training">训练任务</RouterLink>
          <RouterLink class="btn" to="/models">模型工作台</RouterLink>
          <RouterLink class="btn" to="/user">查看用户端</RouterLink>
        </section>
        <RouterView v-else :key="dataSource + route.path" />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import Icon from './components/Icon.vue'
import { dataSource, setDataSource } from './api/client.js'
import { useThemeStore } from './stores/theme.js'
import { fmtTime } from './utils/format.js'
import { authState, signOut } from './stores/auth.js'

const route = useRoute()
const router = useRouter()
const theme = useThemeStore()

// /user 路由（meta.userApp）走独立用户端布局，不渲染调试侧边栏与顶栏
const isUserApp = computed(() => Boolean(route.meta?.userApp) || Boolean(route.meta?.standalone))

const navRoutes = computed(() => router.getRoutes().filter((r) => r.meta?.title && (!r.meta.adminOnly || authState.user?.role === 'admin')))
const logoutError = ref('')
async function logout() {
  logoutError.value = ''
  try { await signOut(); await router.replace('/login') }
  catch (e) { logoutError.value = `退出失败，当前仍登录：${e.message}` }
}
const sourceLabel = computed(() => {
  if (authState.mode === 'protected' && route.name === 'models') return '受保护模式 · 真实模型工作流 · 显式发布'
  if (authState.mode === 'protected' && route.name === 'training') return '受保护模式 · 真实工程任务 · 不自动发布'
  if (authState.mode === 'protected' && route.name === 'accounts') return '受保护模式 · 真实账户管理'
  if (authState.mode === 'protected' && ['overview', 'collector'].includes(route.name)) return '受保护模式 · 后台功能未就绪'
  if (dataSource.value !== 'hybrid') return '演示数据 · 非真实行情'
  if (route.name === 'monitor') return '监控接口数据'
  if (route.name === 'analysis') return '真实行情 · 概览与预测已接入'
  return '接入模式 · 本页仍为演示数据'
})

// 顶栏时钟
const clock = ref('')
let timer = null
function tick() {
  clock.value = fmtTime(new Date().toISOString())
}
onMounted(() => { tick(); timer = setInterval(tick, 1000) })
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
nav {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.source-select select { font-size: 12px; max-width: 150px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
@media (max-width: 800px) {
  .layout { flex-direction: column; height: 100dvh; }
  .sidebar { width: 100%; padding: 8px; }
  .logo, .sidebar-footer { display: none; }
  nav { flex-direction: row; justify-content: space-between; overflow-x: auto; }
  .nav-item { flex-shrink: 0; padding: 8px 6px; font-size: 12px; gap: 4px; }
  .main { min-height: 0; }
  .topbar { height: auto; min-height: 56px; padding: 10px 12px; flex-wrap: wrap; gap: 8px; }
  .topbar-title { font-size: 14px; }
  .topbar-right { flex-wrap: wrap; gap: 8px; }
  .clock { display: none; }
  .content { padding: 12px; }
}
</style>
