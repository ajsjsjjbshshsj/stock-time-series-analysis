import { reactive } from 'vue'
import { authApi } from '../api/auth.js'
import { setRequestIdentity, setProtectedContext } from '../api/client.js'

export const authState = reactive({ mode: null, user: null, ready: false, error: '' })
let pending
let generation = 0

function accept(result) {
  if (!['local', 'protected'].includes(result?.mode) || (result.user && !['user', 'admin'].includes(result.user.role))) throw new Error('认证响应格式无效')
  authState.mode = result.mode
  authState.user = result.user
  authState.ready = true
  authState.error = ''
  setProtectedContext(result.mode === 'protected')
  setRequestIdentity(result.user?.id || 'anonymous')
  return authState
}

export function invalidateAuth() {
  generation++
  pending = null
  authApi.clear()
  authState.user = null
  authState.ready = false
  setRequestIdentity('anonymous')
}

export async function restoreAuth(force = false) {
  if (authState.ready && !force) return authState
  if (!pending || force) {
    const run = ++generation
    pending = authApi.session().then(result => run === generation ? accept(result) : authState)
      .catch(error => { if (run === generation) { authState.error = error.message; authState.ready = false }; throw error })
      .finally(() => { if (run === generation) pending = null })
  }
  return pending
}

export async function signIn(name, password) { return accept(await authApi.login(name, password)) }
export async function signOut() { await authApi.logout(); invalidateAuth() }
