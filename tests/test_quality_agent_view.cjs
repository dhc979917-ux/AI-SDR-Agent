const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const start=source.indexOf('function qualityAgentView(');
const end=source.indexOf('function resultView(',start);
const context={fieldLabels:{shejishi:'设计师人数'},esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),evidenceLinks:evs=>evs.map(e=>e.turn_id).join(' ')};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
test('historical results need no agent metadata',()=>assert.equal(context.qualityAgentView(null),''));
test('shows bounded review actions, manual tasks, evidence and escapes content',()=>{
 const html=context.qualityAgentView({status:'call_budget',calls:3,steps:[{action:'inspect_evidence',field:'shejishi',turn_ids:['T01'],outcome:'<script>bad</script>'}],manual_tasks:[{field:'shejishi',turn_ids:['T02'],message:'请核听'}]});
 assert.match(html,/达到补查次数上限/);assert.match(html,/设计师人数/);assert.match(html,/T01/);assert.match(html,/T02/);assert.match(html,/请核听/);assert.ok(!html.includes('<script>'));
});
