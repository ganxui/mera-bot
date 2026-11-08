import json
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mera import Engine, Store, build_plan, parse_date
from bot import ApiError, ProcessLock, decode_update, event_key, flush_outbox

TODAY = date(2026, 11, 5)


def text(messages):
    return '\n'.join(m['text'] for m in messages)


def buttons(messages):
    return [b for m in messages for a in m.get('attachments', [])
            for row in a['payload']['buttons'] for b in row]


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.engine = Engine(self.store, today=lambda: TODAY)
        self.engine.handle(1, 'create')
        self.engine.handle(1, '9А')
        self.join(2, 'teacher')
        self.join(3, 'student')

    def tearDown(self):
        self.store.close()

    def join(self, uid, role):
        member = self.store.member(1)
        self.engine.handle(uid, 'join')
        return self.engine.handle(uid, member[f'{role}_code'])

    def draft(self, uid=2, minutes=45, due='завтра', subject='История'):
        self.engine.handle(uid, 'add')
        self.engine.handle(uid, subject)
        self.engine.handle(uid, '§12, вопросы 1–3')
        self.engine.handle(uid, due)
        return self.engine.handle(uid, str(minutes))

    def publish(self, uid=2, minutes=45, due='завтра', subject='История'):
        messages = self.draft(uid, minutes, due, subject)
        key = buttons(messages)[0]['payload']
        self.engine.handle(uid, key)
        return self.store.tasks(self.store.member(uid)['class_id'])[-1]

    def test_roles_and_class_creation(self):
        self.assertEqual(self.store.member(1)['role'], 'admin')
        self.assertEqual(self.store.member(2)['role'], 'teacher')
        self.assertEqual(self.store.member(3)['role'], 'student')
        self.assertIn('недоступно', text(self.engine.handle(3, 'add')))
        self.assertIn('недоступно', text(self.engine.handle(2, 'manage')))

    def test_teacher_code_is_not_exposed_to_students(self):
        codes = [self.store.member(1)['student_code'], self.store.member(1)['teacher_code']]
        for action in ('menu', 'help', 'load', 'manage'):
            response = text(self.engine.handle(3, action))
            self.assertTrue(all(c not in response for c in codes))

    def test_publish_only_after_confirmation(self):
        preview = self.draft()
        self.assertEqual(self.store.tasks(1), [])
        self.engine.handle(2, buttons(preview)[0]['payload'])
        self.assertEqual(len(self.store.tasks(1)), 1)
        self.assertIn('История', text(self.engine.handle(3, 'plan')))

    def test_warning_and_move_before_publication(self):
        self.publish(minutes=150)
        preview = self.draft(minutes=60)
        self.assertIn('Высокая нагрузка', text(preview))
        self.assertIn('210 мин', text(preview))
        self.engine.handle(2, next(b['payload'] for b in buttons(preview) if b['payload'].startswith('move:')))
        changed = self.engine.handle(2, 'послезавтра')
        self.assertNotIn('Высокая нагрузка', text(changed))
        self.engine.handle(2, buttons(changed)[0]['payload'])
        self.assertEqual({t['due'] for t in self.store.tasks(1)}, {'2026-11-05', '2026-11-06'})
        self.assertEqual({t['lesson_due'] for t in self.store.tasks(1)}, {'2026-11-06', '2026-11-07'})

    def test_changed_class_load_is_rechecked(self):
        first = self.draft(2, minutes=120)
        self.publish(1, minutes=120)
        response = self.engine.handle(2, buttons(first)[0]['payload'])
        self.assertIn('Нагрузка изменилась', text(response))
        self.assertIn('240 мин', text(response))
        self.assertEqual(len(self.store.tasks(1)), 1)
        self.engine.handle(2, buttons(response)[0]['payload'])
        self.assertEqual(len(self.store.tasks(1)), 2)

    def test_old_confirmation_does_not_duplicate_assignment(self):
        first = self.draft()
        action = buttons(first)[0]['payload']
        self.engine.handle(2, action)
        response = self.engine.handle(2, action)
        self.assertIn('устарела', text(response))
        self.assertEqual(len(self.store.tasks(1)), 1)

    def test_invalid_minutes_and_date_preserve_step(self):
        self.engine.handle(2, 'add')
        self.engine.handle(2, 'Физика')
        self.engine.handle(2, 'Задача 10')
        self.assertIn('дату', text(self.engine.handle(2, '31.02.2027')))
        self.assertEqual(self.store.state(2)['step'], 'due')
        self.engine.handle(2, 'завтра')
        self.assertIn('целое число', text(self.engine.handle(2, '-5')))
        self.assertEqual(self.store.state(2)['step'], 'minutes')

    def test_completion_is_personal_and_does_not_reduce_class_load(self):
        task = self.publish(minutes=45)
        before = text(self.engine.handle(3, 'load'))
        self.engine.handle(3, 'done:' + task['id'])
        self.assertIn('Все добавленные задания выполнены', text(self.engine.handle(3, 'plan')))
        self.assertEqual(before, text(self.engine.handle(3, 'load')))
        self.join(4, 'student')
        self.assertIn('История', text(self.engine.handle(4, 'plan')))

    def test_anonymous_median_requires_three_actual_answers(self):
        task = self.publish()
        for uid, minutes in ((3, 15), (4, 30), (5, 60)):
            if uid != 3:
                self.join(uid, 'student')
            self.engine.handle(uid, 'done:' + task['id'])
            self.engine.handle(uid, f'actual:{task["id"]}:{minutes}')
            if uid != 5:
                self.assertIn('недостаточно ответов', text(self.engine.handle(2, 'tasks')))
        self.assertIn('Медиана: 30 мин (3 ответов)', text(self.engine.handle(2, 'tasks')))

    def test_cross_class_task_access_is_rejected(self):
        task = self.publish()
        self.engine.handle(10, 'create')
        self.engine.handle(10, '10Б')
        self.assertIn('не найдено', text(self.engine.handle(10, 'delete:' + task['id'])))
        self.assertEqual(len(self.store.tasks(1)), 1)

    def test_teacher_cannot_delete_other_teacher_task(self):
        task = self.publish(1)
        self.assertIn('собственные задания', text(self.engine.handle(2, 'delete_confirm:' + task['id'])))
        self.assertEqual(len(self.store.tasks(1)), 1)

    def test_rotating_codes_revokes_old_code_without_affecting_members(self):
        old_code = self.store.member(1)['teacher_code']
        self.engine.handle(1, 'rotate')
        self.engine.handle(8, 'join')
        self.assertIn('Код не найден', text(self.engine.handle(8, old_code)))
        self.assertEqual(self.store.member(2)['role'], 'teacher')

    def test_zero_capacity_reports_overflow(self):
        self.publish(minutes=90)
        self.engine.handle(3, 'free')
        response = self.engine.handle(3, '0 0 0 0 0 0 0')
        self.assertIn('ещё 90 мин', text(response))

    def test_update_is_idempotent_and_replies_are_durable(self):
        self.engine.handle(3, 'plan', event_id='one')
        count = self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
        self.assertGreater(count, 0)
        self.assertEqual(self.engine.handle(3, 'plan', event_id='one'), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], count)

    def test_max_message_sizes(self):
        self.engine.handle(2, 'add')
        self.engine.handle(2, 'П' * 60)
        self.engine.handle(2, 'Т' * 700)
        self.engine.handle(2, 'завтра')
        preview = self.engine.handle(2, '600')
        self.engine.handle(2, buttons(preview)[0]['payload'])
        for action in ('plan', 'load'):
            for message in self.engine.handle(3, action):
                self.assertLessEqual(len(message['text']), 4000)


class PlannerTests(unittest.TestCase):
    def task(self, key, minutes, due):
        return {'id': key, 'minutes': minutes, 'due': due.isoformat()}

    def test_splits_large_assignment_and_respects_deadline(self):
        tasks = [self.task('a', 150, TODAY + timedelta(days=1)), self.task('b', 50, TODAY)]
        plan, overdue, overflow = build_plan(tasks, set(), [100] * 7, TODAY)
        self.assertEqual([e['task']['id'] for e in plan[TODAY.isoformat()]], ['b', 'a'])
        self.assertEqual(sum(e['minutes'] for e in plan[TODAY.isoformat()]), 100)
        self.assertEqual(sum(e['minutes'] for e in plan[(TODAY + timedelta(days=1)).isoformat()]), 100)
        self.assertEqual((overdue, overflow), ([], []))

    def test_overdue_is_not_silently_rescheduled(self):
        plan, overdue, overflow = build_plan([self.task('a', 40, TODAY - timedelta(days=1))], set(), [100] * 7, TODAY)
        self.assertFalse(any(plan.values()))
        self.assertEqual(len(overdue), 1)
        self.assertFalse(overflow)

    def test_capacity_conservation_and_deadlines_for_random_cases(self):
        rng = random.Random(31)
        for _ in range(100):
            available = [rng.randrange(0, 240) for _ in range(7)]
            tasks = [self.task(str(i), rng.randrange(1, 600), TODAY + timedelta(days=rng.randrange(0, 10))) for i in range(12)]
            plan, overdue, overflow = build_plan(tasks, set(), available, TODAY)
            self.assertFalse(overdue)
            assigned = {}
            for day, entries in plan.items():
                self.assertLessEqual(sum(e['minutes'] for e in entries), available[date.fromisoformat(day).weekday()])
                for entry in entries:
                    self.assertLessEqual(day, entry['task']['due'])
                    assigned[entry['task']['id']] = assigned.get(entry['task']['id'], 0) + entry['minutes']
            for entry in overflow:
                key = entry['task']['id']
                assigned[key] = assigned.get(key, 0) + entry['minutes']
            self.assertEqual(assigned, {t['id']: t['minutes'] for t in tasks})

    def test_explicit_year_and_date_boundaries(self):
        self.assertEqual(parse_date('06.11.2026', TODAY), TODAY + timedelta(days=1))
        self.assertEqual(parse_date('завтра', TODAY), TODAY + timedelta(days=1))
        for invalid in ('04.11.2026', '2028-01-01', '31.02.2027'):
            with self.assertRaises(ValueError):
                parse_date(invalid, TODAY)


class TransportTests(unittest.TestCase):
    def test_only_one_process_can_lock_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'db.lock'
            with ProcessLock(path):
                with self.assertRaises(ValueError):
                    with ProcessLock(path):
                        pass
            with ProcessLock(path):
                pass

    def test_private_messages_and_callbacks(self):
        message = {'update_type': 'message_created', 'message': {
            'sender': {'user_id': 3}, 'recipient': {'chat_type': 'dialog'}, 'body': {'text': '/plan', 'mid': 'm1'}}}
        self.assertEqual(decode_update(message), (3, '/plan'))
        self.assertEqual(event_key(message), 'message:m1')
        message['message']['recipient']['chat_type'] = 'chat'
        self.assertIsNone(decode_update(message))
        callback = {'update_type': 'message_callback', 'callback': {'user': {'user_id': 3}, 'payload': 'plan', 'callback_id': 'c1'},
                    'message': {'recipient': {'chat_type': 'dialog'}}}
        self.assertEqual(decode_update(callback), (3, 'plan'))
        self.assertEqual(event_key(callback), 'callback:c1')

    def test_send_failure_keeps_reply_for_retry(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        Engine(store).handle(1, '/start', event_id='start')
        class FakeAPI:
            fail = True
            calls = 0
            def upload(self, path):
                return {'token': 'fake-image'}
            def send(self, uid, message):
                self.calls += 1
                if self.fail:
                    raise ApiError(503)
                return {'message': {'body': {'mid': 'sent-1'}}}
        api = FakeAPI()
        with self.assertRaises(ApiError):
            flush_outbox(store, api)
        self.assertEqual(store.db.execute('SELECT sent FROM outbox').fetchone()[0], 0)
        api.fail = False
        flush_outbox(store, api)
        self.assertEqual(store.db.execute('SELECT sent FROM outbox').fetchone()[0], 1)
        flush_outbox(store, api)
        self.assertEqual(api.calls, 2)

    def test_database_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'db.sqlite3'
            store = Store(path)
            engine = Engine(store)
            engine.handle(1, 'create')
            engine.handle(1, '9А', event_id='create')
            store.close()
            store = Store(path)
            try:
                self.assertEqual(store.member(1)['name'], '9А')
                self.assertEqual(Engine(store).handle(1, '9А', event_id='create'), [])
                self.assertGreater(store.db.execute('SELECT COUNT(*) FROM outbox WHERE sent=0').fetchone()[0], 0)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
