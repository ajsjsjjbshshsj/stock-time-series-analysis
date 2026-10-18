import { createRouter, createWebHashHistory } from 'vue-router'
import { authState, restoreAuth, invalidateAuth } from '../stores/auth.js'
import { authorizeRoute } from '../utils/access.js'
import { setUserContext } from '../api/client.js'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { standalone: true } },
  { path: '/password', name: 'password', component: () => import('../views/LoginView.vue'), meta: { standalone: true } },
  { path: '/accounts', name: 'accounts', component: () => import('../views/admin/AccountsView.vue'), meta: { title: '账户管理', icon: 'users', adminOnly: true } },
  { path: '/training', name: 'training', component: () => import('../views/admin/TrainingTasksView.vue'), meta: { title: '训练任务', icon: 'activity', adminOnly: true } },
  { path: '/', redirect: '/overview' },
  {
    path: '/overview',
    name: 'overview',
    component: () => import('../views/OverviewView.vue'),
    meta: { title: '系统总览', icon: 'grid' },
  },
  {
    path: '/collector',
    name: 'collector',
    component: () => import('../views/CollectorView.vue'),
    meta: { title: '数据采集', icon: 'database' },
  },
  {
    path: '/monitor',
    name: 'monitor',
    component: () => import('../views/MonitorView.vue'),
    meta: { title: 'Kafka 消费监控', icon: 'activity' },
  },
  {
    path: '/analysis',
    name: 'analysis',
    component: () => import('../views/AnalysisView.vue'),
    meta: { title: '分析建模', icon: 'trend' },
  },
  {
    // 用户端入口：无 meta.title → 不出现在调试端侧边栏；
    // meta.userApp → App.vue 据此渲染独立全屏布局（无调试导航/数据模式切换）
    path: '/user',
    name: 'user',
    component: () => import('../views/UserAppView.vue'),
    meta: { userApp: true },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach(async to => {
  try { await restoreAuth() }
  catch { return to.name === 'login' ? true : '/login' }
  const decision = authorizeRoute(to, authState)
  if (decision === true) setUserContext(Boolean(to.meta?.userApp))
  return decision
})
if (typeof window !== 'undefined') window.addEventListener('sas-auth-required', () => {
  if (authState.mode === 'protected') { invalidateAuth(); router.replace('/login') }
})
export default router
