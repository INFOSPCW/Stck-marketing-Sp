from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'
    _order = 'date desc, time_start desc, id desc'

    # ── Begin / End time ──────────────────────────────────────────────────────
    time_start = fields.Float(
        string='Begin', digits=(2, 2),
        help='Start hour for this timesheet entry (e.g. 9.5 = 09:30).',
    )
    time_stop = fields.Float(
        string='End', digits=(2, 2),
        help='End hour for this timesheet entry.',
    )
    use_begin_end = fields.Boolean(
        string='Use Begin/End',
        compute='_compute_use_begin_end',
        help='True when time_start or time_stop are set.',
    )

    # ── Day of week ───────────────────────────────────────────────────────────
    day_week = fields.Selection(
        selection=[
            ('0', 'Monday'),
            ('1', 'Tuesday'),
            ('2', 'Wednesday'),
            ('3', 'Thursday'),
            ('4', 'Friday'),
            ('5', 'Saturday'),
            ('6', 'Sunday'),
        ],
        string='Day',
        compute='_compute_day_week',
        store=True,
    )

    # ── Timer ─────────────────────────────────────────────────────────────────
    timer_start = fields.Datetime(
        string='Timer Started',
        copy=False,
        help='Datetime when the running timer was started.',
    )
    is_running = fields.Boolean(
        string='Timer Running',
        compute='_compute_is_running',
    )

    # ── Task stage ────────────────────────────────────────────────────────────
    is_task_closed = fields.Boolean(
        string='Task Closed',
        related='task_id.stage_id.fold',
        readonly=True,
        store=True,
    )

    # ── Computes ──────────────────────────────────────────────────────────────

    @api.depends('time_start', 'time_stop')
    def _compute_use_begin_end(self):
        for line in self:
            line.use_begin_end = bool(line.time_start or line.time_stop)

    @api.depends('date')
    def _compute_day_week(self):
        for line in self:
            if line.date:
                line.day_week = str(line.date.weekday())
            else:
                line.day_week = False

    def _compute_is_running(self):
        for line in self:
            line.is_running = bool(line.timer_start)

    # ── Begin/End onchanges ───────────────────────────────────────────────────

    @api.onchange('time_start', 'time_stop')
    def _onchange_begin_end(self):
        for line in self:
            if line.time_start and line.time_stop and line.time_stop > line.time_start:
                line.unit_amount = line.time_stop - line.time_start

    @api.constrains('time_start', 'time_stop')
    def _check_begin_end(self):
        for line in self:
            if line.time_start and line.time_stop:
                if line.time_stop < line.time_start:
                    raise ValidationError(_(
                        'End time (%(stop)s) cannot be before begin time (%(start)s) on "%(name)s".',
                        stop=self._float_to_time_str(line.time_stop),
                        start=self._float_to_time_str(line.time_start),
                        name=line.name or '',
                    ))

    @api.constrains('time_start', 'time_stop', 'date', 'employee_id')
    def _check_overlap(self):
        for line in self:
            if not (line.time_start and line.time_stop and line.employee_id and line.date):
                continue
            overlapping = self.search([
                ('id', '!=', line.id),
                ('employee_id', '=', line.employee_id.id),
                ('date', '=', line.date),
                ('time_start', '<', line.time_stop),
                ('time_stop', '>', line.time_start),
            ])
            if overlapping:
                raise ValidationError(_(
                    'Timesheet entry for %(employee)s on %(date)s overlaps with "%(other)s".',
                    employee=line.employee_id.name,
                    date=line.date,
                    other=overlapping[0].name or overlapping[0].project_id.name,
                ))

    # ── Timer actions ─────────────────────────────────────────────────────────

    def action_timer_start(self):
        self.ensure_one()
        if self.timer_start:
            raise ValidationError(_('Timer is already running on this entry.'))
        self.timer_start = fields.Datetime.now()

    def action_timer_stop(self):
        self.ensure_one()
        if not self.timer_start:
            return
        elapsed = fields.Datetime.now() - self.timer_start
        elapsed_hours = elapsed.total_seconds() / 3600.0
        self.unit_amount += round(elapsed_hours, 2)
        self.timer_start = False

    def action_timer_reset(self):
        self.ensure_one()
        self.timer_start = False

    # ── Task stage toggle ─────────────────────────────────────────────────────

    def action_open_task(self):
        self.ensure_one()
        if not self.task_id:
            return
        open_stage = self.env['project.task.type'].search(
            [('project_ids', 'in', self.task_id.project_id.id), ('fold', '=', False)],
            order='sequence asc', limit=1,
        )
        if open_stage:
            self.task_id.stage_id = open_stage

    def action_close_task(self):
        self.ensure_one()
        if not self.task_id:
            return
        closed_stage = self.env['project.task.type'].search(
            [('project_ids', 'in', self.task_id.project_id.id), ('fold', '=', True)],
            order='sequence asc', limit=1,
        )
        if closed_stage:
            self.task_id.stage_id = closed_stage

    def action_toggle_task_stage(self):
        self.ensure_one()
        if self.is_task_closed:
            self.action_open_task()
        else:
            self.action_close_task()

    # ── ORM overrides ─────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._sync_begin_end_to_unit_amount(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._sync_begin_end_to_unit_amount(vals)
        return super().write(vals)

    def _sync_begin_end_to_unit_amount(self, vals):
        start = vals.get('time_start')
        stop = vals.get('time_stop')
        if start is not None or stop is not None:
            start = start if start is not None else (self[:1].time_start if self else 0.0)
            stop = stop if stop is not None else (self[:1].time_stop if self else 0.0)
            if start and stop and stop > start:
                vals['unit_amount'] = round(stop - start, 2)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _float_to_time_str(value):
        hours = int(value)
        minutes = round((value - hours) * 60)
        return f'{hours:02d}:{minutes:02d}'
