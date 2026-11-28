import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';
import { probe } from '../single-conversation-subagent/scripts/zcode-context.mjs';

const parent = 'sess_probe_parent';
const catalog = 'sess_probe_catalog';
const agent = 'agent_11111111-1111-4111-8111-111111111111';
const child = `sess_subagent_${agent}`;

function fixture() {
  const db = new DatabaseSync(':memory:');
  db.exec(`
    CREATE TABLE session (id TEXT, parent_id TEXT, task_type TEXT,
                          last_outcome TEXT, time_compacting INTEGER);
    CREATE TABLE turn_usage (session_id TEXT, turn_id TEXT, status TEXT,
                             context_exceeded INTEGER, started_at INTEGER);
    CREATE TABLE model_usage (id TEXT, session_id TEXT, turn_id TEXT,
      provider_id TEXT, model_id TEXT, status TEXT, input_tokens INTEGER,
      output_tokens INTEGER, context_exceeded INTEGER, completed_at INTEGER,
      query_source TEXT, started_at INTEGER, attempt_index INTEGER);
    CREATE TABLE part (id TEXT, session_id TEXT, time_updated INTEGER, data TEXT);
  `);
  const stamp = Date.now();
  db.prepare("INSERT INTO session VALUES (?, ?, 'subagent_child', 'completed', NULL)").run(child, parent);
  db.prepare("INSERT INTO turn_usage VALUES (?, 'latest-turn', 'completed', 0, ?)").run(child, stamp);
  const insertUsage = db.prepare(`INSERT INTO model_usage VALUES
    (?, ?, ?, 'provider', 'child-model', 'completed', ?, ?, 0, ?, 'subagent', ?, 0)`);
  // Summing these requests would rotate; only the latest request measures context.
  insertUsage.run('old', child, 'old-turn', 4530000, 1000, stamp - 2, stamp - 3);
  insertUsage.run('latest', child, 'latest-turn', 132000, 438, stamp, stamp - 1);
  const display = { kind: 'list_models', models: [
    { id: 'provider/child-model', contextWindow: 1000000 },
  ] };
  const data = { type: 'tool', tool: 'DeferredToolCall', state: {
    input: { name: 'ListModels' }, status: 'completed',
    metadata: { display: JSON.stringify(display) },
  } };
  db.prepare("INSERT INTO part VALUES ('catalog', ?, ?, ?)").run(catalog, stamp, JSON.stringify(data));
  return db;
}

function check(t, setup = () => {}) {
  const db = fixture();
  t.after(() => db.close());
  setup(db);
  return probe(db, parent, agent, catalog, 4096);
}

test('latest request ignores cumulative bill', (t) => {
  const result = check(t);
  assert.equal(result.decision, 'REUSE');
  assert.equal(result.request_id, 'latest');
  assert.equal(result.used_tokens, 132438);
  assert.equal(result.projected_percent, 13.65);
});

for (const [used, decision] of [[695903, 'REUSE'], [695904, 'ROTATE'], [695905, 'ROTATE']]) {
  test(`next-round reserve: ${used} + 4096 returns ${decision}`, (t) => {
    const result = check(t, (db) => {
      db.prepare("UPDATE model_usage SET input_tokens=?, output_tokens=0 WHERE id='latest'").run(used);
    });
    assert.equal(result.decision, decision);
  });
}

function addParentUsage(db, input) {
  const stamp = Date.now() + 1;
  db.prepare("INSERT INTO session VALUES (?, NULL, 'main', 'completed', NULL)").run(parent);
  db.prepare("INSERT INTO turn_usage VALUES (?, 'parent-turn', 'completed', 0, ?)").run(parent, stamp);
  db.prepare(`INSERT INTO model_usage VALUES
    ('parent-request', ?, 'parent-turn', 'provider', 'parent-model', 'completed', ?, 0, 0, ?, 'main', ?, 0)`)
    .run(parent, input, stamp, stamp);
  const row = db.prepare("SELECT data FROM part WHERE id='catalog'").get();
  const data = JSON.parse(row.data);
  const display = JSON.parse(data.state.metadata.display);
  display.models.push({ id: 'provider/parent-model', contextWindow: 200000 });
  data.state.metadata.display = JSON.stringify(display);
  db.prepare("UPDATE part SET data=? WHERE id='catalog'").run(JSON.stringify(data));
}

test('nearly full parent and different parent window do not rotate a small child', (t) => {
  const result = check(t, (db) => addParentUsage(db, 190000));
  assert.equal(result.decision, 'REUSE');
  assert.equal(result.model, 'provider/child-model');
  assert.equal(result.context_window, 1000000);
  assert.equal(result.used_tokens, 132438);
  assert.equal(result.projected_percent, 13.65);
});

test('small parent does not permit reuse of a child above seventy percent', (t) => {
  const result = check(t, (db) => {
    addParentUsage(db, 1000);
    db.prepare("UPDATE model_usage SET input_tokens=750000, output_tokens=0 WHERE id='latest'").run();
  });
  assert.equal(result.decision, 'ROTATE');
  assert.equal(result.model, 'provider/child-model');
  assert.equal(result.projected_percent, 75.41);
});

test('missing model catalog returns UNKNOWN, not zero', (t) => {
  const result = check(t, (db) => db.exec('DELETE FROM part'));
  assert.equal(result.decision, 'UNKNOWN');
  assert.equal(result.reason, 'CONTEXT_WINDOW_UNVERIFIED');
  assert.equal(result.projected_percent, undefined);
});

test('expired model catalog returns UNKNOWN', (t) => {
  const result = check(t, (db) => db.exec('UPDATE part SET time_updated=0'));
  assert.equal(result.decision, 'UNKNOWN');
  assert.equal(result.reason, 'CONTEXT_WINDOW_UNVERIFIED');
});

test('model window must match the child model', (t) => {
  const result = check(t, (db) => {
    db.prepare("UPDATE model_usage SET model_id=? WHERE id='latest'").run('other-model');
  });
  assert.equal(result.decision, 'UNKNOWN');
  assert.equal(result.reason, 'CONTEXT_WINDOW_UNVERIFIED');
});

test('running child is not a completed reusable candidate', (t) => {
  const result = check(t, (db) => db.exec("UPDATE turn_usage SET status='running'"));
  assert.equal(result.decision, 'UNKNOWN');
  assert.equal(result.reason, 'TURN_NOT_VERIFIED');
});

test('child requires the real parent session', (t) => {
  const db = fixture();
  t.after(() => db.close());
  assert.deepEqual(probe(db, 'sess_wrong_parent', agent, catalog, 4096), {
    decision: 'UNKNOWN', reason: 'CHILD_NOT_VERIFIED',
  });
});

test('dispatch guidance precedes intake and survives compaction', () => {
  const skill = readFileSync(new URL('../single-conversation-subagent/SKILL.md', import.meta.url), 'utf8');
  const rules = readFileSync(new URL('../AGENTS.snippet.md', import.meta.url), 'utf8');
  const context = readFileSync(new URL('../single-conversation-subagent/references/zcode-context.md', import.meta.url), 'utf8');
  assert.ok(skill.indexOf('## ZCode 派发前检查') < skill.indexOf('## 开场'));
  for (const text of [skill, rules, context]) {
    assert.ok(text.includes('dispatch_decision'));
    assert.ok(text.includes('ListModels'));
    assert.ok(text.includes('UNKNOWN'));
  }
  assert.ok(skill.includes('70% 只判断目标子 Agent，不判断主对话'));
  assert.ok(skill.includes('累计 token 是账单，不是上下文'));
  assert.ok(skill.includes('同线候选的角色/实际 ID/完成状态'));
  const protocol = readFileSync(new URL('../single-conversation-subagent/references/protocol.md', import.meta.url), 'utf8');
  assert.ok(!protocol.includes('主对话接近 60%–70%'));
});
