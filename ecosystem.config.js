module.exports = {
  apps: [
    {
      name: 'odoo-reports',
      script: '/opt/odoo18/webapp/venv/bin/python',
      args: '/opt/odoo18/webapp/app.py',
      cwd: '/opt/odoo18/webapp',
      interpreter: 'none',
      env: { FLASK_ENV: 'production' },
      restart_delay: 3000,
      max_restarts: 10,
      watch: false,
      error_file: '/opt/odoo18/webapp/logs/err.log',
      out_file:   '/opt/odoo18/webapp/logs/out.log',
    },
    {
      name: 'odoo-mobile-api',
      script: '/opt/odoo18/webapp/venv/bin/python',
      args: '/opt/odoo18/webapp/mobile_api.py',
      cwd: '/opt/odoo18/webapp',
      interpreter: 'none',
      env: { MOBILE_API_PORT: '8800', MOBILE_API_ROOT_PATH: '/mobileapi' },
      restart_delay: 3000,
      max_restarts: 10,
      watch: false,
      error_file: '/opt/odoo18/webapp/logs/mobile_api_err.log',
      out_file:   '/opt/odoo18/webapp/logs/mobile_api_out.log',
    },
  ]
};
