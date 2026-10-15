import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
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

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
