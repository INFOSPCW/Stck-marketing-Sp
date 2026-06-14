{
    'name': 'Timesheet Enhanced',
    'version': '19.0.1.0.0',
    'summary': 'Enhanced timesheets: begin/end time, timer, day-of-week reporting, task stage control',
    'category': 'Human Resources/Timesheets',
    'author': 'Custom',
    'license': 'LGPL-3',
    'depends': [
        'hr_timesheet',
    ],
    'data': [
        'data/timesheet_data.xml',
        'views/account_analytic_line_views.xml',
        'views/project_task_views.xml',
        'views/project_project_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'timesheet_enhanced/static/src/scss/timesheet.scss',
        ],
    },
    'images': ['static/description/icon.png'],
    'application': False,
    'installable': True,
    'auto_install': False,
    'tests': ['tests'],
}
