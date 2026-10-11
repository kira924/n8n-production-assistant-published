// Runs inside the n8n container after the owner account is created.
import {mkdtempSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';

const key = process.env.GROQ_API_KEY || '';
const token = process.env.WORKFLOW_SERVICE_TOKEN || '';
if (!key || token.length < 32) {
  console.error('Configure GROQ_API_KEY and WORKFLOW_SERVICE_TOKEN in your local .env before bootstrapping.');
  process.exit(1);
}
const folder = mkdtempSync(join(tmpdir(),'production-desk-'));
try {
  const file = join(folder,'credentials.json');
  writeFileSync(file,JSON.stringify([
    {id:'productionDeskGroq',name:'Production Desk Groq',type:'groqApi',data:{apiKey:key}},
    {id:'productionDeskWorkflow',name:'Production Desk Workflow',type:'httpHeaderAuth',data:{name:'X-Workflow-Key',value:token}},
  ]),{mode:0o600});
  for(const args of [
    ['import:credentials','--input='+file],
    ['import:workflow','--input=/files/workflows/production-plan.json'],
    ['publish:workflow','--id=productionDeskPublicPlan'],
  ]) execFileSync('n8n',args,{stdio:'pipe',env:process.env});
  console.log('Planning workflow and encrypted credentials configured. Restart n8n to register its production webhook.');
} catch {
  console.error('Bootstrap failed. Confirm the n8n owner account exists and the configured model/key is available.');
  process.exitCode=1;
} finally {
  rmSync(folder,{recursive:true,force:true});
}
