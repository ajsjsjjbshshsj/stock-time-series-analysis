/** Selection is a whole fixed cohort, never a model/member intersection. */
export function poolOptions(pools, kind, search = '') {
  const needle = search.trim().toLocaleLowerCase()
  return pools.filter(pool => (pool.group_type || 'model') === kind &&
    (!needle || `${pool.display_name} ${pool.group_code || ''}`.toLocaleLowerCase().includes(needle)))
}

export function modelOptions(models, version) {
  return models.map(model => ({ ...model,
    compatible: model.pools.some(pool => pool.pool_version === version),
    incompatibility: model.incompatible_pools?.find(pool => pool.pool_version === version),
  }))
}

export function incompatibilityLabel(model) {
  const count = model.incompatibility?.unsupported_stock_count
  return count ? `所选池有${count}只不在此模型冻结范围内` : '未支持此完整股票池'
}

export function poolSourceLabel(pool) {
  if (pool.group_type === 'model' || pool.provider === 'frozen_model') return '模型原始冻结成员，不是当前全市场'
  if (pool.provider_snapshot_date) return `成分快照 ${pool.provider_snapshot_date}（非历史逐日成分）`
  return `本地股票分类，截至 ${pool.requested_as_of || '未提供'}（非历史行业快照）`
}
