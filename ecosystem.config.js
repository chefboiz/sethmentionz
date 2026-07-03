'use strict';
const path = require('path');
const fs   = require('fs');

function loadEnv() {
  const envPath = path.join(__dirname, '.env');
  if (!fs.existsSync(envPath)) return {};
  const env = {};
  fs.readFileSync(envPath, 'utf8').split('\n').forEach(line => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return;
    const idx = trimmed.indexOf('=');
    if (idx === -1) return;
    const key   = trimmed.slice(0, idx).trim();
    const val   = trimmed.slice(idx + 1).trim().replace(/^["']|["']$/g, '');
    if (key) env[key] = val;
  });
  return env;
}

module.exports = {
  apps: [{
    name:               'sethmentionz',
    script:             'main.py',
    interpreter:        'python',
    cwd:                __dirname,
    env:                loadEnv(),
    autorestart:        true,
    watch:              false,
    max_memory_restart: '300M',
    min_uptime:         '10s',
    max_restarts:       10,
    restart_delay:      5000,
    log_date_format:    'YYYY-MM-DD HH:mm:ss Z',
    out_file:           './logs/out.log',
    error_file:         './logs/error.log',
    merge_logs:         true,
  }],
};
