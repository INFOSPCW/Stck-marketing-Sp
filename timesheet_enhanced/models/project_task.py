from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    timesheet_count = fields.Integer(
        string='Timesheets',
        compute='_compute_timesheet_count',
    )

    def _compute_timesheet_count(self):
        data = self.env['account.analytic.line']._read_group(
            domain=[('task_id', 'in', self.ids)],
            groupby=['task_id'],
            aggregates=['__count'],
        )
        counts = {task.id: count for task, count in data}
        for task in self:
            task.timesheet_count = counts.get(task.id, 0)

    def action_view_timesheets(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Timesheets – {self.name}',
            'res_model': 'account.analytic.line',
            'view_mode': 'list,form',
            'domain': [('task_id', '=', self.id)],
            'context': {
                'default_task_id': self.id,
                'default_project_id': self.project_id.id,
            },
        }
