import fs from 'node:fs';
import path from 'node:path';

const root = fs.realpathSync(process.argv[2]);
const files = fs.readFileSync(0, 'utf8').split('\0').filter(Boolean);
const sourceAudit = process.argv[3] === 'true';
const tracked = new Set(files);
const errors = [];
const fail = (message) => errors.push(message);
const required = [
  'AGENTS.md', 'CLAUDE.md', 'CODEX.md', 'README.md', '.gitignore', '.shellcheckrc',
  '.markdownlint-cli2.yaml', '.harness/index.md', '.harness/catalog.md',
  '.harness/rules.md', '.harness/tooling-runtime.md', '.harness/sync-status.json',
  '.harness/update-log.md', '.harness/checklists/doc-sync.md',
  ...['small-change', 'large-change', 'github-planning', 'review-sync',
    'knowledge-sync', 'wiki-update', 'local-bootstrap'].map(n => `.harness/workflows/${n}.md`),
  '.project-wiki/schema.md', '.project-wiki/index.md', '.project-wiki/log.md',
  ...['architecture', 'module', 'feature', 'contract', 'decision', 'method',
    'operating-model'].map(n => `.project-wiki/templates/${n}.md`),
  '.project-wiki/architecture/agent-development-harness.md',
  '.project-wiki/decisions/engineering-principles.md',
  '.github/ISSUE_TEMPLATE/requirement.md', '.github/ISSUE_TEMPLATE/config.yml',
  '.github/pull_request_template.md', '.github/review_convergence_comment.md',
  '.github/workflows/quality.yml', 'scripts/README.md',
  ...['issue_status.sh', 'pr_contract.sh', 'review_convergence.sh',
    'review_convergence.jq'].map(n => `scripts/github/${n}`),
  ...['check.sh', 'check_template.sh', 'check_template.mjs', 'tool-versions.env',
    'install_ci_tools.sh'].map(n => `scripts/quality/${n}`),
  ...['issue_status', 'pr_contract', 'review_convergence', 'quality',
    'template'].map(n => `scripts/tests/test_${n}.sh`),
  'docs/getting-started.md', 'docs/updating.md', 'docs/provenance.md',
  'docs/runbooks/issue-pr-quality-tooling.md', 'docs/examples/single-maintainer.md',
];
for (const name of required) {
  if (!tracked.has(name) || !fs.existsSync(path.join(root, name))) fail(`missing required file: ${name}`);
}
let sourceMarkers = [];
if (sourceAudit) {
  try {
    const provenance = fs.readFileSync(path.join(root, 'docs/provenance.md'), 'utf8');
    const profile = JSON.parse(provenance.match(/```json\n([\s\S]*?)\n```/)[1]);
    sourceMarkers = profile.source_markers;
    if (!Array.isArray(sourceMarkers) || sourceMarkers.length === 0 ||
        sourceMarkers.some(s => typeof s !== 'string' || s.trim().length === 0)) {
      throw new Error('source_markers must be a nonempty array of literal strings');
    }
  } catch (error) {
    fail(`invalid source audit profile: ${error.message}`);
    sourceMarkers = [];
  }
}
const texts = new Map();
for (const name of files) {
  const full = path.join(root, name);
  if (!fs.existsSync(full)) { fail(`tracked file missing from worktree: ${name}`); continue; }
  if (fs.lstatSync(full).isSymbolicLink()) { fail(`template symlink requires explicit review: ${name}`); continue; }
  const buffer = fs.readFileSync(full);
  if (buffer.includes(0)) {
    if (sourceAudit) fail(`unexpected binary extraction asset: ${name}`);
    continue;
  }
  const content = buffer.toString('utf8');
  texts.set(name, content);
  if (name !== 'docs/provenance.md' && sourceMarkers.some(marker =>
    `${name}\n${content}`.toLowerCase().includes(marker.toLowerCase()))) fail(`source-specific residue: ${name}`);
}
function checkPath(from, reference, relativeToRoot = false) {
  if (/^(https?:|mailto:|#)/.test(reference) || /[<>*{}]/.test(reference)) return;
  let ref;
  try { ref = decodeURIComponent(reference.split('#')[0].split('?')[0]); }
  catch { fail(`invalid reference in ${from}: ${reference}`); return; }
  if (!ref) return;
  const resolved = path.resolve(relativeToRoot ? root : path.dirname(path.join(root, from)), ref);
  const rel = path.relative(root, resolved);
  if (rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(ref)) {
    fail(`reference escapes repository in ${from}: ${reference}`); return;
  }
  if (!fs.existsSync(resolved)) { fail(`broken local reference in ${from}: ${reference}`); return; }
  if (!tracked.has(rel) && !files.some(n => n.startsWith(`${rel}/`))) {
    fail(`reference is not tracked in ${from}: ${reference}`);
  }
}
function proseOnly(content) {
  let fence = null;
  return content.split('\n').map(line => {
    const mark = line.match(/^\s*(`{3,}|~{3,})/);
    if (mark) {
      if (!fence) fence = mark[1];
      else if (mark[1][0] === fence[0] && mark[1].length >= fence.length) fence = null;
      return '';
    }
    return fence ? '' : line;
  }).join('\n');
}
const entities = new Map();
for (const [name, content] of texts) {
  if (!name.endsWith('.md') || name === 'docs/provenance.md') continue;
  const prose = proseOnly(content);
  // Repository documents use inline links. Reference-style definitions are also checked.
  for (const match of prose.matchAll(/!?\[[^\]\n]*\]\(([^)\n]+)\)/g)) {
    const target = match[1].replace(/^<|>$/g, '').split(/\s+["']/)[0];
    checkPath(name, target);
  }
  for (const match of prose.matchAll(/^\s*\[[^\]]+\]:\s*(\S+)/gm)) checkPath(name, match[1]);
  if (!name.includes('/templates/')) {
    for (const match of prose.matchAll(/`((?:\.harness|\.project-wiki|\.github|scripts|docs)\/[A-Za-z0-9_./-]+\.(?:md|sh|mjs|jq|json|yml|yaml|env))`/g)) {
      checkPath(name, match[1], true);
    }
  }
  if (name.startsWith('.project-wiki/') && !name.includes('/templates/') && content.startsWith('---\n')) {
    const front = content.split('\n---')[0];
    const id = front.match(/^id:\s*(\S+)$/m)?.[1];
    if (!id) fail(`wiki entity lacks id: ${name}`);
    else if (entities.has(id)) fail(`duplicate wiki id: ${id}`);
    else entities.set(id, { name, front });
    for (const section of ['Summary', 'Source Of Truth', 'Contracts', 'Workflows', 'Failure Modes', 'Update Rules', 'Open Questions']) {
      if (!content.includes(`\n## ${section}\n`)) fail(`missing wiki section ${section}: ${name}`);
    }
    const owners = front.match(/^owners:\n((?:[ \t]+.*\n)*)/m)?.[1] ?? '';
    for (const match of owners.matchAll(/^\s*-\s+(\S+)/gm)) checkPath(name, match[1], true);
    for (const match of front.matchAll(/^\s*-\s*path:\s*(\S+)/gm)) checkPath(name, match[1], true);
  }
}
for (const { name, front } of entities.values()) {
  const related = front.match(/^related:\n((?:[ \t]+.*\n)*)/m)?.[1] ?? '';
  for (const match of related.matchAll(/^\s*-\s+(\S+)/gm)) {
    if (!entities.has(match[1])) fail(`unknown wiki related entity ${match[1]}: ${name}`);
  }
}
const workflow = texts.get('.github/workflows/quality.yml') ?? '';
if (!/^\s+run: scripts\/quality\/check\.sh\s*$/m.test(workflow)) fail('CI does not invoke the offline quality entrypoint');
for (const match of workflow.matchAll(/uses:\s+([^\s]+)@([^\s]+)/g)) {
  if (!/^[0-9a-f]{40}$/.test(match[2])) fail(`CI action is not commit-pinned: ${match[1]}`);
}
try { JSON.parse(texts.get('.harness/sync-status.json') ?? ''); }
catch { fail('invalid sync-status JSON'); }
if (errors.length) {
  for (const error of errors) console.error(`ERROR: ${error}`);
  process.exit(1);
}
console.log(`OK: template integrity (${files.length} tracked files, ${entities.size} wiki entities)`);
