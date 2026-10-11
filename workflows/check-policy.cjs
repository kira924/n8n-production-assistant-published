'use strict';
const assert = require('node:assert/strict');
const workflow = require('./production-plan.json');
const names = {
  normalize: 'Normalize Input', parseAgent: 'Parse Agent Result',
  gate: 'Verify Quote and Enforce Policy',
};
const source = Object.fromEntries(Object.entries(names).map(([key, name]) => {
  const node = workflow.nodes.find(value => value.name === name);
  assert.ok(node?.parameters?.jsCode, 'Missing code node: ' + name);
  return [key, node.parameters.jsCode];
}));
// Planning ends at a private callback; only the gateway accepts human decisions.
const hook = workflow.nodes.find(node => node.name === 'Production Request');
assert.equal(hook.parameters.authentication, 'headerAuth');
assert.equal(hook.parameters.responseMode, 'onReceived');
assert.equal(workflow.nodes.some(node => node.name === 'Create Work Order'), false);
function run(code, incoming, named = {}) {
  return new Function('$input', '$execution', '$', '$now', code)(
    { first: () => ({ json: incoming }) },
    { id: 'test-100' },
    name => ({ first: () => ({ json: named[name] }) }),
    { toISODate: () => '2026-10-11' },
  )[0].json;
}
const raw = { brief: 'Record a two-hour synthetic interview with two cameras.', production_date: '2026-10-20', duration_hours: '2', camera_count: '2', include_audio: 'true', editing_hours: '2', budget_sar: '3000' };
const input = run(source.normalize, raw);
assert.equal(input.status, 'ready');
assert.equal(input.include_audio, true);
assert.equal(run(source.normalize, { ...raw, production_date: '2026-99-99' }).status, 'needs_info');
assert.equal(run(source.normalize, { ...raw, budget_sar: '' }).status, 'needs_info');
assert.equal(run(source.normalize, { ...raw, include_audio: 'yes' }).status, 'needs_info');
assert.equal(run(source.normalize, { ...raw, duration_hours: '2.5' }).status, 'needs_info');
assert.equal(run(source.normalize, { ...raw, camera_count: '4' }).status, 'needs_info');
assert.equal(run(source.normalize, { ...raw, camera_count: '3' }).status, 'ready');
assert.equal(run(source.normalize, { ...raw, production_date: '2026-10-10' }).status, 'needs_info');
assert.equal(run(source.normalize, { ...raw, production_date: '2027-10-13' }).status, 'needs_info');
const quoted = { ...input, quote_id: 'q_test', claimed_total_sar: 2240, brief_summary: 'Two-camera interview', missing_fields: [] };
const fields = Object.fromEntries(['request_id', 'brief', 'production_date', 'duration_hours', 'camera_count', 'include_audio', 'editing_hours'].map(name => [name, input[name]]));
const quote = { quote_id: 'q_test', request_id: input.request_id, fields, currency: 'SAR', synthetic: true, total_sar: 2240, available: true, line_items: [{ line_total_sar: 2240 }] };
const response = { statusCode: 200, body: { verified: true, quote } };
function gate(value = response, agent = quoted) { return run(source.gate, value, { 'Parse Agent Result': agent }); }
assert.equal(gate().policy_allowed, true);
assert.equal(gate(response, { ...quoted, budget_sar: 2000 }).status, 'rejected');
assert.equal(gate({ ...response, body: { verified: true, quote: { ...quote, available: false } } }).status, 'rejected');
assert.equal(gate({ ...response, body: { verified: true, quote: { ...quote, fields: { ...fields, camera_count: 1 } } } }).status, 'error');
assert.equal(gate(response, { ...quoted, claimed_total_sar: 2230 }).status, 'error');
assert.equal(gate({ statusCode: 409, body: { detail: 'mismatch' } }).status, 'error');
assert.equal(gate({ error: { message: 'offline' } }).status, 'error');
const catalog = { synthetic: true, currency: 'SAR', services: [{ code: 'studio', unit_price_sar: 350 }] };
const availability = { synthetic: true, production_date: input.production_date, available: true,
  requested: { duration_hours: input.duration_hours, camera_count: input.camera_count, include_audio: input.include_audio } };
function observation(tool, body) { return { action: { tool, toolInput: {} }, observation: JSON.stringify([body]) }; }
const steps = [observation('catalog', catalog), observation('availability', availability), observation('quote', quote)];
const natural = { output: 'Prepare a two-camera interview, capture clean audio, then edit the footage. The resources are available.', intermediateSteps: steps };
function parseAgent(value) { return run(source.parseAgent, value, { 'Normalize Input': input }); }
const parserCases = [
  ['natural-language plan with real tool observations', natural, 'quoted'],
  ['fabricated quote in final text without real observations', { output: JSON.stringify({ status: 'quoted', quote_id: 'q_fake', claimed_total_sar: 1 }) }, 'error'],
  ['missing tool', { ...natural, intermediateSteps: steps.slice(1) }, 'error'],
  ['wrong tool order', { ...natural, intermediateSteps: [steps[1], steps[0], steps[2]] }, 'error'],
  ['extra tool call', { ...natural, intermediateSteps: [...steps, steps[2]] }, 'error'],
  ['malformed quote observation', { ...natural, intermediateSteps: [...steps.slice(0, 2), { ...steps[2], observation: 'invalid JSON' }] }, 'error'],
  ['unexpected quote observation envelope', { ...natural, intermediateSteps: [...steps.slice(0, 2), { ...steps[2], observation: JSON.stringify(quote) }] }, 'error'],
  ['non-string quote ID', { ...natural, intermediateSteps: [...steps.slice(0, 2), observation('quote', { ...quote, quote_id: 123 })] }, 'error'],
  ['non-numeric quote total', { ...natural, intermediateSteps: [...steps.slice(0, 2), observation('quote', { ...quote, total_sar: '2240' })] }, 'error'],
  ['model-supplied tool parameters', { ...natural, intermediateSteps: [{ ...steps[0], action: { tool: 'catalog', toolInput: { input: 'override' } } }, ...steps.slice(1)] }, 'error'],
  ['tool quote changed original form fields', { ...natural, intermediateSteps: [...steps.slice(0, 2), observation('quote', { ...quote, fields: { ...fields, camera_count: 1 } })] }, 'error'],
  ['unavailable result remains eligible for deterministic rejection', { ...natural, intermediateSteps: [steps[0], observation('availability', { ...availability, available: false }), observation('quote', { ...quote, available: false })] }, 'quoted'],
];
for (const [name, value, expected] of parserCases) assert.equal(parseAgent(value).status, expected, name);
const ignoredFake = parseAgent({ ...natural, output: 'A fabricated narrative mentions q_fake and 1 SAR, but observations are authoritative.' });
assert.equal(ignoredFake.quote_id, quote.quote_id);
assert.equal(ignoredFake.claimed_total_sar, quote.total_sar);
console.log('Published planning policies passed: input validation, observed tool sequence, canonical quote binding, budget and availability.');
