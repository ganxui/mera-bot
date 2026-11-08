import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from estimation import DeepSeekEstimator
from mera import Engine, Store


def buttons(messages):
    return [
        button
        for message in messages
        for attachment in message.get('attachments', [])
        for row in attachment['payload']['buttons']
        for button in row
    ]


class EstimateFlowTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.estimate = Mock(return_value={'minutes': 35, 'kind': 'решение задач'})
        self.engine = Engine(self.store, today=lambda: date(2026, 11, 8), estimator=self.estimate)
        self.engine.handle(1, 'create')
        self.engine.handle(1, '9А')

    def enter_assignment(self):
        for text in ('add', 'Алгебра', 'Решить 10 квадратных уравнений'):
            self.engine.handle(1, text)
        return self.engine.handle(1, 'lesson:2026-11-09')

    def publish(self, messages):
        confirm = next(button['payload'] for button in buttons(messages) if button['payload'].startswith('confirm:'))
        self.engine.handle(1, confirm)

    def test_estimate_is_a_suggestion_until_teacher_accepts_and_publishes(self):
        messages = self.enter_assignment()
        self.estimate.assert_called_once_with('Алгебра', 'Решить 10 квадратных уравнений', 9)
        self.assertIn('решение задач', messages[0]['text'])
        self.assertEqual(messages[0]['_visual']['minutes'], 35)
        self.assertFalse(self.store.tasks(1))
        accepted = next(button['payload'] for button in buttons(messages) if button['payload'].startswith('estimate:'))
        preview = self.engine.handle(1, accepted)
        self.assertIn('35 мин с этим заданием', preview[0]['text'])
        self.assertFalse(self.store.tasks(1))
        self.publish(preview)
        self.assertEqual(self.store.tasks(1)[0]['minutes'], 35)

    def test_manual_minutes_win_and_repeat_offers_previous_duration(self):
        self.enter_assignment()
        self.engine.handle(1, 'own_minutes')
        self.publish(self.engine.handle(1, '50'))
        self.assertEqual(self.store.tasks(1)[0]['minutes'], 50)
        self.engine.handle(1, 'repeat_last')
        repeat = self.engine.handle(1, 'lesson:2026-11-10')
        self.assertIn('Ориентир · 35 мин', repeat[0]['text'])
        self.assertIn('Оставить 50 мин', [item['text'] for item in buttons(self.engine.handle(1, 'own_minutes'))])

    def test_failure_keeps_manual_entry_available(self):
        self.estimate.return_value = None
        messages = self.enter_assignment()
        self.assertIn('Не удалось оценить время', messages[0]['text'])
        self.publish(self.engine.handle(1, 'minutes:20'))
        self.assertEqual(self.store.tasks(1)[0]['minutes'], 20)

    def test_body_edit_replaces_the_stale_suggestion(self):
        initial = self.enter_assignment()
        stale = next(button['payload'] for button in buttons(initial) if button['payload'].startswith('estimate:'))
        preview = self.engine.handle(1, '50')
        edit = next(button['payload'] for button in buttons(preview) if button['payload'].startswith('draft_body:'))
        self.engine.handle(1, edit)
        self.estimate.return_value = {'minutes': 12, 'kind': 'решение задач'}
        current = self.engine.handle(1, 'Решить 3 квадратных уравнения')
        self.assertIn('12 мин', current[0]['text'])
        self.assertIn('устарела', self.engine.handle(1, stale)[0]['text'])
        accepted = next(button['payload'] for button in buttons(current) if button['payload'].startswith('estimate:'))
        self.publish(self.engine.handle(1, accepted))
        self.assertEqual(self.store.tasks(1)[0]['minutes'], 12)


class EstimateAdapterTests(unittest.TestCase):
    @patch('estimation.urllib.request.urlopen')
    def test_only_assignment_data_is_sent_and_kind_is_mapped_locally(self, urlopen):
        envelope = {'choices': [{'message': {'content': json.dumps({'minutes': 35, 'kind': 'foreign text'})}}]}
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
        result = DeepSeekEstimator('test-key')('Алгебра', 'Решить 10 уравнений', 9)
        self.assertEqual(result, {'minutes': 35, 'kind': 'смешанное задание'})
        request = urlopen.call_args.args[0]
        data = json.loads(request.data)
        self.assertEqual(json.loads(data['messages'][1]['content']),
                         {'subject': 'Алгебра', 'assignment': 'Решить 10 уравнений', 'grade': 9})

    @patch('estimation.urllib.request.urlopen')
    def test_invalid_duration_and_timeout_return_no_suggestion(self, urlopen):
        envelope = {'choices': [{'message': {'content': '{"minutes": 601, "kind": "problems"}'}}]}
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
        adapter = DeepSeekEstimator('test-key')
        with self.assertLogs(level='WARNING'):
            self.assertIsNone(adapter('Алгебра', 'Уравнения', 9))
        urlopen.side_effect = TimeoutError
        with self.assertLogs(level='WARNING'):
            self.assertIsNone(adapter('Алгебра', 'Уравнения', 9))
