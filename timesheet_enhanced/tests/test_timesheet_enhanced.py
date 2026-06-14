"""
Tests for timesheet_enhanced module.

Covers:
- Begin/End time fields and auto-sync to unit_amount (create + write)
- use_begin_end computed field
- day_week computed stored field
- Constraint: end before start raises ValidationError
- Constraint: overlapping entries for same employee/date raises ValidationError
- Timer: start, stop (accumulates elapsed hours), reset, double-start error
- is_running computed field
- Task stage: close, open, toggle (action_close_task / action_open_task / action_toggle_task_stage)
- is_task_closed related stored field
- project.task: timesheet_count + action_view_timesheets
- _float_to_time_str helper
"""

import datetime
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestTimesheetEnhanced(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # ── Project + stages ──────────────────────────────────────────────────
        cls.project = cls.env['project.project'].create({
            'name': 'Test Project TE',
            'allow_timesheets': True,
        })

        cls.stage_open = cls.env['project.task.type'].create({
            'name': 'In Progress',
            'fold': False,
            'sequence': 1,
            'project_ids': [(4, cls.project.id)],
        })
        cls.stage_closed = cls.env['project.task.type'].create({
            'name': 'Done',
            'fold': True,
            'sequence': 10,
            'project_ids': [(4, cls.project.id)],
        })

        # ── Task ──────────────────────────────────────────────────────────────
        cls.task = cls.env['project.task'].create({
            'name': 'Test Task TE',
            'project_id': cls.project.id,
            'stage_id': cls.stage_open.id,
        })

        # ── Employee / analytic account ───────────────────────────────────────
        cls.employee = cls.env['hr.employee'].create({'name': 'TE Employee'})

        # Ensure the project has an analytic account (required for timesheets).
        # In Odoo 19 the field is account_id, not analytic_account_id.
        if not cls.project.account_id:
            cls.project._create_analytic_account()

    def _make_line(self, **kwargs):
        """Shortcut to create a timesheet line with sensible defaults."""
        defaults = {
            'name': 'Test line',
            'project_id': self.project.id,
            'task_id': self.task.id,
            'employee_id': self.employee.id,
            'date': datetime.date(2025, 1, 6),   # Monday
            'unit_amount': 0.0,
        }
        defaults.update(kwargs)
        return self.env['account.analytic.line'].create(defaults)

    # =========================================================================
    # 1.  Begin / End → unit_amount sync on CREATE
    # =========================================================================

    def test_create_syncs_unit_amount_from_begin_end(self):
        line = self._make_line(time_start=9.0, time_stop=11.5)
        self.assertAlmostEqual(line.unit_amount, 2.5,
                               msg='unit_amount should equal time_stop - time_start on create')

    def test_create_no_sync_when_stop_missing(self):
        line = self._make_line(time_start=9.0, unit_amount=3.0)
        self.assertAlmostEqual(line.unit_amount, 3.0,
                               msg='unit_amount should not be overwritten when time_stop is absent')

    def test_create_no_sync_when_stop_before_start(self):
        # Should raise constraint, but let's verify sync logic doesn't write negative amount
        # The constraint check runs after create; we test this via _sync directly.
        vals = {'time_start': 11.0, 'time_stop': 9.0, 'unit_amount': 5.0}
        self.env['account.analytic.line']._sync_begin_end_to_unit_amount(vals)
        self.assertAlmostEqual(vals['unit_amount'], 5.0,
                               msg='Sync must not update unit_amount when stop ≤ start')

    # =========================================================================
    # 2.  Begin / End → unit_amount sync on WRITE
    # =========================================================================

    def test_write_syncs_unit_amount(self):
        line = self._make_line(unit_amount=1.0)
        line.write({'time_start': 8.0, 'time_stop': 10.0})
        self.assertAlmostEqual(line.unit_amount, 2.0,
                               msg='unit_amount should be updated on write with begin/end')

    def test_write_partial_update_uses_existing_start(self):
        line = self._make_line(time_start=8.0, time_stop=10.0, unit_amount=2.0)
        line.write({'time_stop': 11.0})
        self.assertAlmostEqual(line.unit_amount, 3.0,
                               msg='Partial write of time_stop should use existing time_start')

    # =========================================================================
    # 3.  use_begin_end computed field
    # =========================================================================

    def test_use_begin_end_true_when_start_set(self):
        line = self._make_line(time_start=9.0)
        self.assertTrue(line.use_begin_end)

    def test_use_begin_end_true_when_stop_set(self):
        line = self._make_line(time_stop=17.0)
        self.assertTrue(line.use_begin_end)

    def test_use_begin_end_false_when_neither_set(self):
        line = self._make_line()
        self.assertFalse(line.use_begin_end)

    # =========================================================================
    # 4.  day_week computed + stored field
    # =========================================================================

    def test_day_week_monday(self):
        line = self._make_line(date=datetime.date(2025, 1, 6))   # Monday
        self.assertEqual(line.day_week, '0')

    def test_day_week_friday(self):
        line = self._make_line(date=datetime.date(2025, 1, 10))  # Friday
        self.assertEqual(line.day_week, '4')

    def test_day_week_sunday(self):
        line = self._make_line(date=datetime.date(2025, 1, 12))  # Sunday
        self.assertEqual(line.day_week, '6')

    def test_day_week_updates_on_date_change(self):
        line = self._make_line(date=datetime.date(2025, 1, 6))   # Monday → '0'
        line.date = datetime.date(2025, 1, 7)                    # Tuesday
        self.assertEqual(line.day_week, '1')

    # =========================================================================
    # 5.  Constraint: end before start
    # =========================================================================

    def test_constraint_end_before_start_raises(self):
        with self.assertRaises(ValidationError, msg='End before start must raise ValidationError'):
            self._make_line(time_start=12.0, time_stop=9.0)

    def test_constraint_equal_times_no_raise(self):
        """Equal start/stop (0-duration) is not 'before', so no error is expected."""
        self._make_line(time_start=9.0, time_stop=9.0)  # must not raise

    def test_constraint_valid_times_no_raise(self):
        # Should not raise
        self._make_line(time_start=9.0, time_stop=17.0)

    # =========================================================================
    # 6.  Constraint: overlapping entries
    # =========================================================================

    def test_overlap_same_employee_same_day_raises(self):
        self._make_line(time_start=9.0, time_stop=11.0, name='First')
        with self.assertRaises(ValidationError, msg='Overlapping timesheet must raise ValidationError'):
            self._make_line(time_start=10.0, time_stop=12.0, name='Second')

    def test_overlap_adjacent_entries_no_raise(self):
        """Entries that touch (end == next start) must NOT overlap."""
        self._make_line(time_start=9.0, time_stop=11.0, name='Morning')
        # 11.0 → 13.0: start equals previous stop — should be fine
        self._make_line(time_start=11.0, time_stop=13.0, name='Afternoon')

    def test_overlap_different_employee_no_raise(self):
        employee2 = self.env['hr.employee'].create({'name': 'TE Employee 2'})
        self._make_line(time_start=9.0, time_stop=11.0, name='Emp1')
        # Same time slot, different employee — must not raise
        self._make_line(time_start=9.0, time_stop=11.0, name='Emp2', employee_id=employee2.id)

    def test_overlap_different_date_no_raise(self):
        self._make_line(time_start=9.0, time_stop=11.0,
                        date=datetime.date(2025, 1, 6), name='Day1')
        self._make_line(time_start=9.0, time_stop=11.0,
                        date=datetime.date(2025, 1, 7), name='Day2')

    # =========================================================================
    # 7.  Timer: start / stop / reset / is_running
    # =========================================================================

    def test_timer_start_sets_timer_start_field(self):
        line = self._make_line()
        self.assertFalse(line.is_running)
        line.action_timer_start()
        self.assertTrue(bool(line.timer_start))
        # is_running is a non-stored computed field — invalidate before re-reading
        line.invalidate_recordset(['is_running'])
        self.assertTrue(line.is_running)

    def test_timer_start_twice_raises(self):
        line = self._make_line()
        line.action_timer_start()
        with self.assertRaises(ValidationError, msg='Starting timer twice must raise ValidationError'):
            line.action_timer_start()

    def test_timer_stop_accumulates_hours(self):
        line = self._make_line(unit_amount=1.0)
        fake_start = fields_datetime_now = datetime.datetime(2025, 1, 6, 9, 0, 0)
        fake_stop = datetime.datetime(2025, 1, 6, 10, 30, 0)   # 1.5 h later

        from odoo import fields as odoo_fields
        with patch.object(odoo_fields.Datetime, 'now', side_effect=[fake_start, fake_stop]):
            line.action_timer_start()
            line.action_timer_stop()

        self.assertFalse(line.timer_start, 'timer_start should be cleared after stop')
        self.assertFalse(line.is_running)
        self.assertAlmostEqual(line.unit_amount, 2.5, places=1,
                               msg='unit_amount should have 1.5 h added (1.0 initial + 1.5 elapsed)')

    def test_timer_stop_no_op_when_not_running(self):
        line = self._make_line(unit_amount=3.0)
        line.action_timer_stop()   # should not raise or change anything
        self.assertAlmostEqual(line.unit_amount, 3.0)

    def test_timer_reset_clears_timer_start(self):
        line = self._make_line()
        line.action_timer_start()
        line.invalidate_recordset(['is_running'])
        self.assertTrue(line.is_running)
        line.action_timer_reset()
        self.assertFalse(line.timer_start)
        line.invalidate_recordset(['is_running'])
        self.assertFalse(line.is_running)

    def test_timer_reset_does_not_update_unit_amount(self):
        line = self._make_line(unit_amount=2.0)
        line.action_timer_start()
        line.action_timer_reset()
        self.assertAlmostEqual(line.unit_amount, 2.0,
                               msg='reset must not accumulate time into unit_amount')

    # =========================================================================
    # 8.  Task stage: close / open / toggle
    # =========================================================================

    def test_action_close_task(self):
        line = self._make_line()
        self.assertEqual(self.task.stage_id, self.stage_open)
        line.action_close_task()
        self.assertEqual(self.task.stage_id, self.stage_closed,
                         msg='action_close_task must move task to a folded stage')

    def test_action_open_task(self):
        self.task.stage_id = self.stage_closed
        line = self._make_line()
        line.action_open_task()
        self.assertEqual(self.task.stage_id, self.stage_open,
                         msg='action_open_task must move task to an unfolded stage')

    def test_action_toggle_closes_open_task(self):
        self.task.stage_id = self.stage_open
        line = self._make_line()
        line.action_toggle_task_stage()
        self.assertTrue(self.task.stage_id.fold, 'toggle on open task must close it')

    def test_action_toggle_opens_closed_task(self):
        self.task.stage_id = self.stage_closed
        line = self._make_line()
        # Force refresh of is_task_closed stored field
        line.invalidate_recordset(['is_task_closed'])
        line.action_toggle_task_stage()
        self.assertFalse(self.task.stage_id.fold, 'toggle on closed task must re-open it')

    def test_action_close_no_task_no_raise(self):
        line = self._make_line(task_id=False)
        line.action_close_task()  # must silently do nothing

    def test_action_open_no_task_no_raise(self):
        line = self._make_line(task_id=False)
        line.action_open_task()

    # =========================================================================
    # 9.  is_task_closed stored related field
    # =========================================================================

    def test_is_task_closed_reflects_stage_fold(self):
        line = self._make_line()
        self.task.stage_id = self.stage_open
        line.invalidate_recordset(['is_task_closed'])
        self.assertFalse(line.is_task_closed)

        self.task.stage_id = self.stage_closed
        line.invalidate_recordset(['is_task_closed'])
        self.assertTrue(line.is_task_closed)

    def test_is_task_closed_false_when_no_task(self):
        line = self._make_line(task_id=False)
        self.assertFalse(line.is_task_closed)

    # =========================================================================
    # 10. project.task: timesheet_count + action_view_timesheets
    # =========================================================================

    def test_timesheet_count_zero_initially(self):
        task2 = self.env['project.task'].create({
            'name': 'Empty Task',
            'project_id': self.project.id,
        })
        self.assertEqual(task2.timesheet_count, 0)

    def test_timesheet_count_increments(self):
        task3 = self.env['project.task'].create({
            'name': 'Counted Task',
            'project_id': self.project.id,
            'stage_id': self.stage_open.id,
        })
        self.env['account.analytic.line'].create([
            {
                'name': 'TS 1', 'project_id': self.project.id,
                'task_id': task3.id, 'employee_id': self.employee.id,
                'date': datetime.date(2025, 1, 6), 'unit_amount': 1.0,
            },
            {
                'name': 'TS 2', 'project_id': self.project.id,
                'task_id': task3.id, 'employee_id': self.employee.id,
                'date': datetime.date(2025, 1, 7), 'unit_amount': 2.0,
            },
        ])
        self.assertEqual(task3.timesheet_count, 2)

    def test_action_view_timesheets_returns_act_window(self):
        action = self.task.action_view_timesheets()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'account.analytic.line')
        self.assertIn(('task_id', '=', self.task.id), action['domain'])
        self.assertEqual(action['context']['default_task_id'], self.task.id)
        self.assertEqual(action['context']['default_project_id'], self.project.id)

    # =========================================================================
    # 11. _float_to_time_str helper
    # =========================================================================

    def test_float_to_time_str_whole_hours(self):
        from odoo.addons.timesheet_enhanced.models.account_analytic_line import AccountAnalyticLine
        self.assertEqual(AccountAnalyticLine._float_to_time_str(9.0), '09:00')
        self.assertEqual(AccountAnalyticLine._float_to_time_str(14.0), '14:00')

    def test_float_to_time_str_half_hour(self):
        from odoo.addons.timesheet_enhanced.models.account_analytic_line import AccountAnalyticLine
        self.assertEqual(AccountAnalyticLine._float_to_time_str(9.5), '09:30')

    def test_float_to_time_str_quarter_hour(self):
        from odoo.addons.timesheet_enhanced.models.account_analytic_line import AccountAnalyticLine
        self.assertEqual(AccountAnalyticLine._float_to_time_str(10.25), '10:15')

    def test_float_to_time_str_midnight(self):
        from odoo.addons.timesheet_enhanced.models.account_analytic_line import AccountAnalyticLine
        self.assertEqual(AccountAnalyticLine._float_to_time_str(0.0), '00:00')
