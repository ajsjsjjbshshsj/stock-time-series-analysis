/** Fixed-pool preparation is a real explicit queue command, never a mock. */
import {authApi} from './auth.js'
const BASE='/api/admin/preparation/requests'
function id(value){if(typeof value!=='string'||!/^[0-9a-f]{32}$/.test(value))throw Error('准备任务编号无效');return value}
function page({limit=20,offset=0}={}){if(!Number.isSafeInteger(limit)||limit<1||limit>50||!Number.isSafeInteger(offset)||offset<0||offset>100000)throw Error('准备任务分页无效');return {limit,offset}}
function date(value){if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value)||new Date(value+'T00:00:00Z').toISOString().slice(0,10)!==value||value<'2023-01-01')throw Error('准备日期无效');return value}
export function createPreparationApi(client=authApi){
  const get=(path,params,options)=>client.request(path+(params?'?'+new URLSearchParams(params):''),'GET',undefined,options)
  return {
    list:(options={})=>get(BASE,page(options),options),
    get:(value,options)=>get(`${BASE}/${id(value)}`,null,options),
    tasks:(value,options={})=>get(`${BASE}/${id(value)}/tasks`,page(options),options),
    create:(value,key,options={})=>{
      if(!value||Object.keys(value).sort().join(',')!=='end,pool_version,start'||typeof value.pool_version!=='string'||!/^(pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})$/.test(value.pool_version))throw Error('请选择有效的固定股票池')
      const start=date(value.start),end=date(value.end);if(start>end)throw Error('准备日期范围无效')
      return client.request(BASE,'POST',{pool_version:value.pool_version,start,end},{...options,headers:{'Idempotency-Key':id(key)}})
    },
    retry:(value,options)=>client.request(`${BASE}/${id(value)}/retry`,'POST',{},options),
    retryTask:(parent,task,key,options={})=>client.request(`${BASE}/${id(parent)}/tasks/${id(task)}/retry`,'POST',{},
      {...options,headers:{'Idempotency-Key':id(key)}}),
  }
}
export const preparationApi=createPreparationApi()
