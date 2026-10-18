/** Convenience navigation only. The backend independently authorizes every API. */
export function authorizeRoute(to, auth) {
  if (auth.mode === 'local') return ['accounts', 'training'].includes(to.name) ? '/overview' : true
  if (!auth.user) return to.name === 'login' ? true : '/login'
  if (auth.user.must_change_password) return to.name === 'password' ? true : '/password'
  if (to.name === 'login' || to.name === 'password') return auth.user.role === 'admin' ? '/overview' : '/user'
  if (auth.user.role !== 'admin' && !to.meta?.userApp) return '/user'
  return true
}
