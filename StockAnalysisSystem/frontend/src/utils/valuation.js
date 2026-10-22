/** Missing rows are a successful null result; failures must remain visible. */
export function valuationErrorMessage(error) {
  if (error.status === 404) return '股票或估值接口未找到，请检查股票代码和后端部署版本'
  if (error.status === 503) return '估值服务暂不可用，请稍后重试'
  return `估值数据暂不可用（${error.message}）`
}
