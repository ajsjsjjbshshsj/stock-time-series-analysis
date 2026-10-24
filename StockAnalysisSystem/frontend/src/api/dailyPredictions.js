/** Real admin metadata API; never mock or trigger computation on GET. */
import {authApi} from './auth.js'
const base='/api/admin/daily-predictions'
function id(value){if(typeof value!=='string'||!/^[0-9a-f]{32}$/.test(value))throw Error('版本或计划编号无效');return value}
export const dailyPredictionApi={
  list:options=>authApi.request(base,'GET',undefined,options),
  create:(model,options)=>authApi.request(base,'POST',{model_id:id(model)},options),
  enabled:(plan,enabled,options)=>authApi.request(`${base}/${id(plan)}/enabled`,'POST',{enabled:enabled?'true':'false'},options),
}
