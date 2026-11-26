import { DatabaseSync } from 'node:sqlite';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { parseArgs } from 'node:util';

const sessionId = /^(?:sess|session)_[A-Za-z0-9._-]{1,192}$/;
const agentId = /^agent_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const validCount = (n) => Number.isSafeInteger(n) && n >= 0 && n <= 1_000_000_000;
const unknown = (reason, details = {}) => ({ decision: 'UNKNOWN', reason, ...details });

function catalogWindow(db, session, model) {
  const row = db.prepare(`
    SELECT time_updated, json_extract(data, '$.state.status') AS status,
           json_extract(data, '$.state.metadata.display') AS display
    FROM part WHERE session_id = ? AND json_extract(data, '$.type') = 'tool'
      AND (json_extract(data, '$.tool') = 'ListModels'
        OR (json_extract(data, '$.tool') = 'DeferredToolCall'
          AND json_extract(data, '$.state.input.name') = 'ListModels'))
    ORDER BY time_updated DESC, id DESC LIMIT 1
  `).get(session);
  if (!row || row.status !== 'completed' || !row.display ||
      Date.now() - row.time_updated > 300_000 || row.time_updated > Date.now()) return;
  const display = JSON.parse(row.display);
  if (display?.kind !== 'list_models' || !Array.isArray(display.models)) return;
  const matches = display.models.filter((entry) => entry?.id === model);
  const window = matches[0]?.contextWindow;
  if (matches.length === 1 && validCount(window) && window > 0) return window;
}

export function probe(db, parent, agent, catalogSession, reserve) {
  const child = `sess_subagent_${agent}`;
  const session = db.prepare(`
    SELECT last_outcome, time_compacting FROM session
    WHERE id = ? AND parent_id = ? AND task_type = 'subagent_child'
  `).get(child, parent);
  if (!session) return unknown('CHILD_NOT_VERIFIED');

  const turn = db.prepare(`
    SELECT turn_id, status, context_exceeded FROM turn_usage
    WHERE session_id = ? ORDER BY started_at DESC, turn_id DESC LIMIT 1
  `).get(child);
  if (!turn || !['completed', 'error'].includes(turn.status)) return unknown('TURN_NOT_VERIFIED');
  const details = { child_session_id: child, turn_id: turn.turn_id };
  if (turn.context_exceeded) return { decision: 'ROTATE', reason: 'CONTEXT_EXCEEDED', ...details };

  const usage = db.prepare(`
    SELECT id, turn_id, provider_id, model_id, status, input_tokens, output_tokens,
           context_exceeded, completed_at FROM model_usage
    WHERE session_id = ? AND query_source = 'subagent'
    ORDER BY started_at DESC, attempt_index DESC, id DESC LIMIT 1
  `).get(child);
  if (!usage || usage.turn_id !== turn.turn_id) return unknown('USAGE_NOT_VERIFIED', details);
  details.request_id = usage.id;
  if (usage.context_exceeded) return { decision: 'ROTATE', reason: 'CONTEXT_EXCEEDED', ...details };
  if (session.last_outcome !== 'completed' || session.time_compacting !== null ||
      turn.status !== 'completed' || usage.status !== 'completed') return unknown('CHILD_NOT_READY', details);

  const { provider_id: provider, model_id: model, input_tokens: input, output_tokens: output } = usage;
  if (!provider || !model || !usage.completed_at || !validCount(input) || !input || !validCount(output)) {
    return unknown('USAGE_NOT_USABLE', details);
  }
  const modelRef = `${provider}/${model}`;
  // Cached input is already included in the host's input_tokens.
  const used = input + output;
  Object.assign(details, { model: modelRef, measured_at_ms: usage.completed_at,
    input_tokens: input, output_tokens: output, used_tokens: used });
  const window = catalogWindow(db, catalogSession, modelRef);
  if (!window) return unknown('CONTEXT_WINDOW_UNVERIFIED', details);

  const projected = used + reserve;
  const percent = (tokens) => Number((100 * tokens / window).toFixed(2));
  return { decision: 10 * projected >= 7 * window ? 'ROTATE' : 'REUSE', ...details,
    context_window: window, used_percent: percent(used), reserve_tokens: reserve,
    projected_percent: percent(projected) };
}

function main() {
  const { values } = parseArgs({ options: {
    parent: { type: 'string' }, agent: { type: 'string' }, 'catalog-session': { type: 'string' },
    reserve: { type: 'string', default: '4096' },
  } });
  const catalogSession = values['catalog-session'] ?? values.parent;
  const reserve = Number(values.reserve);
  if (!sessionId.test(values.parent ?? '') || !sessionId.test(catalogSession ?? '') ||
      !agentId.test(values.agent ?? '') || !/^\d+$/.test(values.reserve) || !validCount(reserve)) {
    throw new Error('Expected --parent SESSION --agent AGENT [--catalog-session SESSION] [--reserve TOKENS]');
  }
  let db;
  try {
    db = new DatabaseSync(join(homedir(), '.zcode', 'cli', 'db', 'db.sqlite'), { readOnly: true });
    db.exec('BEGIN');
    return probe(db, values.parent, values.agent, catalogSession, reserve);
  } catch {
    return unknown('HOST_USAGE_UNAVAILABLE');
  } finally {
    db?.close();
  }
}

if (import.meta.main) console.log(JSON.stringify(main()));
