module.exports = {
  apps: [{
    name: 'opencode-web',
    script: 'server.js',
    cwd: __dirname,
    instances: 1,
    exec_mode: 'fork',
    autorestart: true,
    // Do not tree-kill the detached tmux server when restarting the web bridge.
    trekill: false,
    kill_timeout: 12000,
    max_memory_restart: '512M',
    time: true,
    env: { NODE_ENV: 'production' },
  }],
};
