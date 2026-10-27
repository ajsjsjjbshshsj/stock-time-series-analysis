/** Real authenticated prediction only, not training or mock endpoints. */
import {authApi} from './auth.js'
const BASE='/api/analysis/user-predictions'
function id(value,n=64){if(typeof value!=='string'||!new RegExp(`^[0-9a-f]{${n}}$`).test(value))throw Error('预测或模型编号无效');return value}
export function createUserPredictionApi(client=authApi){return {
  catalog:options=>client.request(BASE+'/catalog','GET',undefined,options),
  submit:(value,options)=>{
    if(!value||Object.keys(value).sort().join(',')!=='model_id,pool_version'||typeof value.pool_version!=='string'||!/^(model_[0-9a-f]{32}|pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})$/.test(value.pool_version))throw Error('请选择股票池和模型，不接受训练参数')
    return client.request(BASE,'POST',{model_id:id(value.model_id,32),pool_version:value.pool_version},options)
  },
  status:(value,options)=>client.request(BASE+'/'+id(value),'GET',undefined,options),
  scores:(value,{after=0,signal}={})=>{
    if(!Number.isSafeInteger(after)||after<0||after>10000000)throw Error('预测分页无效')
    return client.request(`${BASE}/${id(value)}/scores?limit=50&after=${after}`,'GET',undefined,{signal})
  },
  releases:options=>client.request('/api/admin/prediction-models','GET',undefined,options),
  release:(model,enabled,options)=>{if(typeof enabled!=='boolean')throw Error('请明确模型上线状态');return client.request('/api/admin/prediction-models/'+id(model,32),'POST',{enabled},options)},
}}
export const userPredictionApi=createUserPredictionApi()
