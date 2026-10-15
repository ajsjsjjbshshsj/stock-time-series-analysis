<template>
  <div>
    <section class="card section">
      <h1>账户管理</h1>
      <p class="muted">只有用户与管理员两类账户。新账户及重置密码后需先改密；权限变更会使该账户的旧会话失效。</p>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <form class="account-form" @submit.prevent="create">
        <label>新账号<input v-model="draft.username" required minlength="3" maxlength="64" autocomplete="off" /></label>
        <label>初始密码<input v-model="draft.password" type="password" required minlength="12" maxlength="128" autocomplete="new-password" /></label>
        <label>角色<select v-model="draft.role"><option value="user">用户</option><option value="admin">管理员</option></select></label>
        <button class="btn btn-primary" :disabled="busy">创建账户</button>
      </form>
    </section>
    <section class="card section">
      <h2>现有账户</h2><button class="btn" :disabled="busy" @click="load">刷新列表</button>
      <div class="accounts-table"><table><thead><tr><th>账号</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>
        <tr v-for="user in users" :key="user.id">
          <td>{{ user.username }} <small v-if="user.must_change_password">待改密</small></td>
          <td><select :value="user.role" :disabled="busy" :aria-label="`${user.username} 的角色`" @change="update(user.id, { role: $event.target.value })"><option value="user">用户</option><option value="admin">管理员</option></select></td>
          <td>{{ user.enabled ? '启用' : '停用' }}</td>
          <td><button class="btn btn-sm" :disabled="busy" @click="update(user.id, { enabled: !user.enabled })">{{ user.enabled ? '停用' : '启用' }}</button></td>
        </tr>
      </tbody></table></div>
    </section>
    <section class="card section">
      <h2>重置账户密码</h2>
      <form class="account-form" @submit.prevent="resetPassword">
        <label>账户<select v-model="resetId" required><option value="" disabled>选择账户</option><option v-for="user in users" :key="user.id" :value="user.id">{{ user.username }}</option></select></label>
        <label>临时密码<input v-model="resetValue" type="password" autocomplete="new-password" required minlength="12" maxlength="128" /></label>
        <button class="btn" :disabled="busy">重置密码并撤销旧会话</button>
      </form>
    </section>
  </div>
</template>
<script setup>
import { onMounted, reactive, ref } from 'vue'
import { authApi } from '../../api/auth.js'
const users = ref([]), busy = ref(false), error = ref(''), notice = ref(''), resetId = ref(''), resetValue = ref('')
const draft = reactive({ username: '', password: '', role: 'user' })
async function load() { try { users.value = await authApi.users() } catch (e) { error.value = e.message } }
async function action(operation) {
  busy.value = true; error.value = ''; notice.value = ''
  try { await operation(); notice.value = '已保存；旧会话已按规则失效。'; await load() }
  catch (e) { error.value = e.message; await load() }
  finally { busy.value = false; draft.password = ''; resetValue.value = '' }
}
function create() { return action(() => authApi.createUser({ ...draft })) }
function update(id, body) { return action(() => authApi.updateUser(id, body)) }
function resetPassword() { return update(resetId.value, { new_password: resetValue.value }) }
onMounted(load)
</script>
<style scoped>
.account-form { display: flex; flex-wrap: wrap; align-items: end; gap: 16px; margin-top: 20px; }.account-form label { display: grid; gap: 8px; flex: 1; min-width: 180px; }
.accounts-table { overflow-x: auto; margin-top: 20px; }table { width: 100%; border-collapse: collapse; }th,td { text-align: left; padding: 12px; border-bottom: 1px solid var(--border); }[role=alert] { color: var(--down); }small { color: var(--muted); }
</style>
