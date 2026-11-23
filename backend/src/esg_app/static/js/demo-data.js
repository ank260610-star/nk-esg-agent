// Human reference values from the handoff, never an extraction implementation.
export const sample = {
  company: '比亚迪股份有限公司', reportYear: 2024,
  title: '比亚迪2024年度可持续发展报告',
  source: 'https://static.cninfo.com.cn/finalpage/2025-03-25/1222881489.PDF',
  reference: 'docs/superpowers/plans/2026-11-19-esg-agent-implementation.md §6',
  metrics: [
    {id:'E01', name:'Scope 1+2 温室气体排放量', unit:'tCO₂e', page:127, printed:126, values:{2023:'12341455.00',2024:'10101826.20'}, note:'分项口径与组织边界需回原文核对。'},
    {id:'E02', name:'Scope 1+2 排放强度', unit:'tCO₂e/万元营收', page:127, printed:126, values:{2023:'0.2049',2024:'0.1300'}, note:'分母为万元营收；不能解释为每辆车的排放。'},
    {id:'E03', name:'Scope 3 温室气体排放量', unit:'tCO₂e', page:127, printed:126, values:{2023:'/',2024:'3326312.49'}, note:'2023年原符号为“/”，不能按0计算同比。Scope 3脚注仅含差旅、通勤、燃料能源相关活动，不能称完整价值链。'},
    {id:'E04', name:'能源总耗量', unit:'MWh', page:125, printed:124, values:{2023:'16592592.44',2024:'23552569.28'}, note:'保留报告披露口径，不默认扣除绿证。'},
    {id:'E05', name:'总取水量', unit:'m³', page:126, printed:125, values:{2023:'73807222',2024:'94268627'}, note:'取水、排水与耗水不能混用；不同年度范围须核对。'},
    {id:'E06', name:'总耗水量', unit:'m³', page:126, printed:125, values:{2023:'22067452',2024:'55317707'}, note:'披露值与系统推导值应分开保存，算术关系不能证明计量方法。'},
  ],
};
export const checkTitles = [
  ['C01','碳排放总量与强度'],['C02','Scope 3覆盖类别与限制'],['C03','能源总量与构成'],['C04','取水与耗水披露'],
  ['Q01','原文数值与单位'],['Q02','记录年度明确'],['Q03','强度分母与数量级'],['Q04','缺失与估算标识'],
  ['B01','组织边界与排除项'],['B02','核算标准与方法'],['B03','排放因子与换算依据'],['B04','估算与不确定性'],
  ['Y01','核心指标历史值'],['Y02','单位与定义可对齐'],['Y03','边界及方法变化'],['Y04','重述与可比性说明'],
  ['T01','目标基准年与基准值'],['T02','目标年度与目标值'],['T03','目标类型与范围'],['T04','目标进展与计算依据'],
];
export function createDemoState() {
  return {revision:1, snapshotRevision:1, years:['2023','2024'], run:'ready', local:false,
    metrics:sample.metrics.map(m=>({...m,values:{...m.values},confirmed:{},reasons:{}})),
    checks:checkTitles.map(([id,title],i)=>({id,title,status:i===0?'met':i===1?'partial':'unknown'})),
    score:{reference_score:'75.00',coverage:'12.50',lower_bound:'9.375',upper_bound:'96.875',final_score:null},
    history:[], stale:false, hasReview:false};
}
export function displayNumber(value) {
  if (!/^-?\d+(\.\d+)?$/.test(value)) return value;
  const [whole,fraction] = value.split('.');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction === undefined ? '' : `.${fraction}`);
}
export function safeCsvCell(value) {
  const text = String(value ?? '');
  // Spreadsheet formulas can also follow tabs, spaces or carriage returns.
  const escaped = /^[\s]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text) ? `'${text}` : text;
  return `"${escaped.replaceAll('"','""')}"`;
}
