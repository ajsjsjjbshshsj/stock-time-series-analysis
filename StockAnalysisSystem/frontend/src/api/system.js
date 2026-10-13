/**
 * 系统总览接口层。
 * 真实端点（部分已存在）：
 * 下列接口为设计约定，后端暂未提供；健康检查统一使用 api/consumer。
 */
import { request } from './client'
import { getServices as mockServices, getKpis as mockKpis } from '../mock/system'

/** GET /api/system/services（设计约定） */
export function getServices() {
  return request(() => mockServices(), '/api/system/services')
}

/** GET /api/system/kpis（设计约定） */
export function getKpis() {
  return request(() => mockKpis(), '/api/system/kpis')
}
