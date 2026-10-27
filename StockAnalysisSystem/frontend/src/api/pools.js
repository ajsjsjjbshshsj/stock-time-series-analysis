/** Always-real administrator pool reads and explicit local snapshot save. */
import { authApi } from './auth.js'
const BASE='/api/admin/pools'
function version(v){if(typeof v!=='string'||! /^(pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})$/.test(v))throw Error('股票池版本无效');return v}
function paging({limit=20,offset=0}={}){if(!Number.isSafeInteger(limit)||limit<1||limit>50||!Number.isSafeInteger(offset)||offset<0||offset>100000)throw Error('股票池分页无效');return {limit,offset}}
export function createPoolApi(client=authApi){
  const get=(path,params,options)=>client.request(`${BASE}/${path}${params?'?'+new URLSearchParams(params):''}`,'GET',undefined,options)
  return {
    catalog:options=>get('catalog',null,options),
    candidate:(selector,options)=>get('candidate',selector,options),
    save:(body,options)=>{version(body.expected_version);return client.request(`${BASE}/snapshots`,'POST',body,options)},
    versions:(options={})=>get('versions',paging(options),options),
    members:(v,options={})=>get('members',{version:version(v),...paging(options)},options),
    coverage:(v,{start,end,...options})=>get('coverage',{version:version(v),start,end,...paging(options)},options),
  }
}
export const poolApi=createPoolApi()
