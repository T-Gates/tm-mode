#!/usr/bin/env node
'use strict';
/*
 * tm-mode npm 스킨 — install.sh 의 JS 포팅(Node stdlib 만, 의존성 0).
 * 태그 핀 raw cli.py 를 받아 python3 로 실행한다. 실제 설치·위저드는 전부
 * cli.py(→ 레포 안 infra/install.py) 몫 — 이 파일은 전달자다.
 *
 * 계약(tests/test_npm_wrapper.py):
 *  - PIN_REF 는 package.json version·src/teammode/__init__.__version__ 과 동기(릴리스 루틴).
 *  - TEAMMODE_CLI_URL(file:// 포함) override — 테스트·미러.
 *  - 빈 다운로드 = exit 2 (무동작 성공 위장 금지 — install.sh 계약).
 *  - TTY: 파이프 실행이어도 제어 TTY 가 있으면 /dev/tty 재연결(POSIX) — 위저드 parity.
 */
const { spawnSync, execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const https = require('https');

const PIN_REF = 'refs/tags/v0.1.3'; // 릴리스마다 __version__ 과 함께 bump
const CLI_URL = process.env.TEAMMODE_CLI_URL ||
  `https://raw.githubusercontent.com/T-Gates/tm-mode/${PIN_REF}/src/teammode/cli.py`;

function fail(msg, code) { process.stderr.write(`[error] ${msg}\n`); process.exit(code); }

function findPython() {
  const candidates = process.platform === 'win32'
    ? [['py', ['-3']], ['python3', []], ['python', []]]
    : [['python3', []], ['python', []]];
  for (const [cmd, pre] of candidates) {
    try {
      execFileSync(cmd, [...pre, '-c',
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'],
        { stdio: 'ignore' });
      return [cmd, pre];
    } catch (e) { /* 다음 후보 */ }
  }
  return null;
}

function cacheDir() {
  if (process.platform === 'win32')
    return path.join(process.env.LOCALAPPDATA || os.tmpdir(), 'tm-mode', 'Cache');
  const base = process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache');
  return path.join(base, 'tm-mode');
}

function fetchTo(url, dest, cb) {
  if (url.startsWith('file://')) {
    try { fs.copyFileSync(new URL(url), dest); } catch (e) { return cb(e); }
    return cb(null);
  }
  const req = https.get(url, (res) => {
    if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location)
      return fetchTo(res.headers.location, dest, cb);
    if (res.statusCode !== 200)
      return cb(new Error(`HTTP ${res.statusCode} — ${url}`));
    const out = fs.createWriteStream(dest);
    res.pipe(out);
    out.on('finish', () => out.close(() => cb(null)));
    out.on('error', cb);
  });
  req.on('error', cb);
  req.setTimeout(30000, () => req.destroy(new Error('download timeout')));
}

function run(cliPath) {
  const py = findPython();
  if (!py) fail('python3(3.9+) 가 필요합니다 — https://www.python.org/downloads/ ' +
    '(macOS: `brew install python3`)', 2);
  const [cmd, pre] = py;
  // TTY 재연결(install.sh parity): 파이프 실행이어도 제어 TTY 가 있으면 위저드가 뜬다.
  let stdin = 'inherit';
  if (process.platform !== 'win32' && !process.stdin.isTTY) {
    try { stdin = fs.openSync('/dev/tty', 'r'); } catch (e) { /* 진짜 비대화 — 그대로 */ }
  }
  const r = spawnSync(cmd, [...pre, cliPath, ...process.argv.slice(2)],
    { stdio: [stdin, 'inherit', 'inherit'], shell: false });
  if (typeof stdin === 'number') { try { fs.closeSync(stdin); } catch (e) {} }
  process.exit(r.status === null ? 1 : r.status);
}

function main() {
  const dir = cacheDir();
  const ver = PIN_REF.replace(/^refs\/tags\//, '');
  const cliPath = path.join(dir, `cli-${ver}.py`);
  const fresh = process.env.TEAMMODE_CLI_URL // override 는 항상 새로 받기(테스트 결정성)
    || !fs.existsSync(cliPath) || fs.statSync(cliPath).size === 0;
  if (!fresh) return run(cliPath);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = cliPath + `.dl-${process.pid}`;
  fetchTo(CLI_URL, tmp, (err) => {
    if (err) { try { fs.unlinkSync(tmp); } catch (e) {}
      fail(`cli.py 다운로드 실패: ${err.message}`, 2); }
    if (!fs.existsSync(tmp) || fs.statSync(tmp).size === 0) {
      try { fs.unlinkSync(tmp); } catch (e) {}
      fail('다운로드된 cli.py 가 비어 있습니다(네트워크/프록시 확인) — 중단', 2);
    }
    fs.renameSync(tmp, cliPath);
    run(cliPath);
  });
}

main();
