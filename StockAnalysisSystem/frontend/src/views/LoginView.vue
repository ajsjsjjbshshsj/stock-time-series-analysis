<template>
  <main class="access-page">
    <section class="access-intro">
      <div class="access-mark" aria-hidden="true">S</div>
      <p class="muted">股票时序分析 · 研究工作台</p>
      <h1>{{ changing ? '更新账户密码' : '登录后查看真实结果' }}</h1>
      <p>行情、模型和发布日期都有来源。训练只在后台执行，用户页面只读取已发布结果。</p>
      <div class="access-roles"><span>用户</span><p>行情查询 · 已发布预测 · 历史研究</p><span>管理员</span><p>账户管理 · 后台任务与审核入口</p></div>
    </section>
    <section class="card access-form">
      <h2>{{ changing ? '首次登录需要更换密码' : '账户登录' }}</h2>
      <p v-if="authState.mode === 'local'" role="status">当前是本机兼容模式，尚未启用远程认证。管理员需按部署说明配置认证后才能使用登录功能。</p>
      <p v-if="error || authState.error" role="alert">{{ error || authState.error }}</p>
      <form v-if="authState.mode !== 'local'" @submit.prevent="submit">
        <label v-if="!changing" for="account-name">账号</label>
        <input v-if="!changing" id="account-name" v-model="name" autocomplete="username" required maxlength="64" />
        <label for="account-password">{{ changing ? '当前密码' : '密码' }}</label>
        <input id="account-password" v-model="password" type="password" autocomplete="current-password" required maxlength="128" />
        <template v-if="changing">
          <label for="new-password">新密码（12～128 个字符）</label>
          <input id="new-password" v-model="replacement" type="password" autocomplete="new-password" required minlength="12" maxlength="128" />
        </template>
        <button class="btn btn-primary" type="submit" :disabled="busy || !authState.ready">{{ busy ? '处理中…' : changing ? '更换密码并重新登录' : '登录' }}</button>
      </form>
      <button v-if="!authState.ready" class="btn" @click="retry">重新连接认证服务</button>
      <RouterLink v-if="authState.mode === 'local'" class="btn" to="/overview">进入本机工作台</RouterLink>
      <p class="muted">不提供公开注册或默认密码。需要账户或忘记密码，请联系管理员。</p>
    </section>
  </main>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { authState, restoreAuth, signIn, invalidateAuth } from '../stores/auth.js'
import { authApi } from '../api/auth.js'

const route = useRoute(), router = useRouter()
const changing = computed(() => route.name === 'password')
const name = ref(''), password = ref(''), replacement = ref(''), busy = ref(false), error = ref('')
async function retry() { try { await restoreAuth(true) } catch (e) { error.value = e.message } }
async function submit() {
  busy.value = true; error.value = ''
  try {
    if (changing.value) {
      await authApi.password(password.value, replacement.value)
      invalidateAuth(); await restoreAuth(true); await router.replace('/login')
    } else {
      const result = await signIn(name.value, password.value)
      await router.replace(result.user.must_change_password ? '/password' : result.user.role === 'admin' ? '/overview' : '/user')
    }
  } catch (e) { error.value = e.message }
  finally { password.value = ''; replacement.value = ''; busy.value = false }
}
</script>

<style scoped>
.access-page { min-height: 100dvh; display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(280px, 420px); align-items: center; gap: 64px; max-width: 1120px; margin: auto; padding: 48px 32px; }
.access-intro h1 { font-size: clamp(28px, 3vw, 42px); line-height: 1.25; margin: 24px 0; }
.access-intro > p { max-width: 500px; line-height: 1.8; }
.access-mark { border: 1px solid var(--border); border-radius: 12px; width: 48px; height: 48px; display: grid; place-items: center; font-size: 24px; font-weight: 700; }
.access-roles { border-left: 3px solid var(--accent); padding-left: 20px; margin-top: 32px; }
.access-roles span { font-weight: 700; }.access-roles p { margin: 6px 0 20px; color: var(--muted); }
.access-form { padding: 32px; }.access-form form { display: grid; gap: 12px; margin: 24px 0; }
.access-form input { width: 100%; min-height: 42px; }.access-form .btn { min-height: 42px; margin-top: 12px; }
.access-form > p { line-height: 1.7; font-size: 13px; }[role=alert] { color: var(--down); }
@media(max-width: 760px) { .access-page { grid-template-columns: 1fr; gap: 24px; padding: 24px 16px; }.access-roles { display: none; }.access-intro h1 { margin: 12px 0; } }
</style>
