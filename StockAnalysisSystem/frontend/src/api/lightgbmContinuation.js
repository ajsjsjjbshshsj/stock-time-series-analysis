import { authApi } from './auth.js'

const PATH = '/api/admin/lightgbm-continuations'
const id = value => typeof value === 'string' && /^[0-9a-f]{32}$/.test(value)

export function createContinuationApi(client = authApi) {
  return {
    parents: options => client.request(PATH, 'GET', undefined, options),
    create(body, key) {
      if (Object.keys(body).length !== 3 || !id(body.parent_model_id) || !id(body.feature_build_id)
          || !Number.isInteger(body.additional_rounds) || body.additional_rounds < 1 || body.additional_rounds > 20
          || typeof key !== 'string' || !/^[A-Za-z0-9_-]{16,64}$/.test(key)) {
        throw new Error('请确认父模型、特征版本和追加轮数（1–20）')
      }
      return client.request(PATH, 'POST', body, { headers: { 'Idempotency-Key': key } })
    },
  }
}

export const continuationApi = createContinuationApi()
