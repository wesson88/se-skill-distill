#!/usr/bin/env node
/**
 * postinstall:把 skill 文件复制到全局 ~/.claude/skills/se-skill-distill/。
 *
 * 设计决策:
 *  - 纯文件分发(用户决策),不做环境检测 —— skillmind 是否装、vault 是否设,
 *    由 skill 运行时的 preflight(scripts/preflight.py)前置检查并给指引,
 *    不在 npm install 阶段强制。
 *  - 幂等:目标已存在先删再复制。
 *  - 失败不抛错(避免 npm install 整体失败),只打印。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');

const PKG_ROOT = __dirname;
const DEST = path.join(os.homedir(), '.claude', 'skills', 'se-skill-distill');
const ASSETS = ['SKILL.md', 'README.md', 'sources.yaml', 'scripts', 'references', 'calibration', 'tests'];

try {
  if (fs.existsSync(DEST)) fs.rmSync(DEST, { recursive: true, force: true });
  fs.mkdirSync(DEST, { recursive: true });
  for (const name of ASSETS) {
    const src = path.join(PKG_ROOT, name);
    if (fs.existsSync(src)) fs.cpSync(src, path.join(DEST, name), { recursive: true });
  }
  console.log('\n  ✅ se-skill-distill 装到 ' + DEST);
  console.log('  ⚠️  使用前请确保:');
  console.log('       ① export SE_VAULT=<你的 vault 根>');
  console.log('       ② skillmind 可 import(pip install skillmind,或 export SKILLMIND_REPO=<本地 skillMind 仓库>)');
  console.log('     skill 启动时 preflight 会再检查这两项并给出指引。\n');
} catch (e) {
  console.error('  se-skill-distill postinstall:复制失败 -', e.message);
  console.error('  可手动复制 ' + PKG_ROOT + ' 下的 SKILL.md/scripts/... 到 ' + DEST);
}
