"""Route natural-language requests through the existing class and plan workflows."""
import re
import secrets
from datetime import timedelta

from mera import ROLE_LABELS, WEEKDAYS, button, class_key, parse_date, reply


ENTRY = 'mera_agent'
ALIASES = ('/agent', 'mera agent', 'мера агент', 'агент')
CLASS_LABEL = r'\d{1,2}\s*[-–]?\s*[а-яa-z]'
ADD_HEADER = re.compile(
    rf'^(?:добавь(?:те)?|задай(?:те)?|создай(?:те)?)\s+'
    rf'(?:(?:в\s+)?(?:класс\s+)?(?P<class_name>{CLASS_LABEL})\s+)?'
    r'(?:дз|(?:домашнее )?задание)\s+по\s+(?P<subject>.+)$', re.IGNORECASE)
ADD_TARGET = re.compile(
    rf'^(?:добавь(?:те)?|задай(?:те)?|создай(?:те)?)\s+'
    rf'(?:в\s+)?(?:класс\s+)?({CLASS_LABEL})\s+(?:дз|(?:домашнее )?задание)\b',
    re.IGNORECASE)
ADD_SUBJECT = re.compile(
    rf'^(?:добавь|создай|внеси)(?:те)?\s+'
    rf'(?:(?:(?:в|для)\s+)?(?:класс[ауе]?\s+)?(?P<before>{CLASS_LABEL})\s+|(?:классу|в класс)\s+)?'
    rf'(?:новый\s+)?предмет\s*[:—-]?\s+(?P<subject>.+?)'
    rf'(?:\s+(?:в|для)\s+(?:класс[ае]?\s+)?(?P<after>{CLASS_LABEL}))?$',
    re.IGNORECASE)
MINUTES = re.compile(
    r'\s+на\s+(\d{1,3})\s*(?:(мин(?:ут[ауы]?)?)|ч(?:ас(?:а|ов)?)?'
    r'(?:\s+(\d{1,2})\s*мин(?:ут[ауы]?)?)?)(?:\.|\s|$)', re.IGNORECASE)
LESSON = re.compile(r'\s+(?:к|до|на)\s+(.+)$', re.IGNORECASE)
PRIVATE = re.compile(r'\b[0-9a-f]{10}\b|[\w.+-]+@[\w.-]+\.\w+|(?<!\w)(?:\+7|8)[\s()\d-]{10,}\d', re.IGNORECASE)
SUBJECT_NAMES = {
    'алгебре': 'Алгебра', 'математике': 'Математика', 'геометрии': 'Геометрия',
    'истории': 'История', 'физике': 'Физика', 'химии': 'Химия', 'биологии': 'Биология',
    'литературе': 'Литература', 'информатике': 'Информатика',
    'русскому': 'Русский язык', 'русскому языку': 'Русский язык',
    'английскому': 'Английский язык', 'английскому языку': 'Английский язык',
}
VIEWS = {
    'load': 'load', 'tasks': 'tasks', 'completed': 'completed', 'availability': 'free',
    'today_time': 'today_time', 'manage': 'manage', 'quick': 'add_quick',
    'repeat': 'repeat_last', 'part': 'plan',
    'students': 'students:0', 'student_tasks': 'student_tasks:0',
}
TASK_ACTIONS = ('edit', 'delete', 'done', 'undo', 'actual')
SHORTCUTS = {
    'нагрузка': 'load', 'нагрузка класса': 'load', 'нагрузку класса': 'load',
    'задания': 'tasks', 'дз': 'tasks', 'домашку': 'tasks', 'задания класса': 'tasks', 'статистику': 'tasks',
    'мой план': 'plan', 'план': 'plan', 'выполненные': 'completed', 'выполнено': 'completed',
    'моё время': 'availability', 'мое время': 'availability',
    'время на сегодня': 'today_time', 'личный кабинет': 'profile', 'профиль': 'profile',
    'управление классом': 'manage', 'настройки класса': 'manage', 'коды класса': 'manage',
    'коды': 'manage', 'кабинет': 'profile',
    'ученики': 'students', 'учеников': 'students', 'список учеников': 'students',
    'отчёт по заданиям': 'student_tasks', 'отчет по заданиям': 'student_tasks',
    'предметы класса': 'class_subjects', 'список предметов': 'class_subjects',
    'мои классы': 'classes', 'все классы': 'classes', 'список классов': 'classes',
    'нагрузку всех классов': 'all_load', 'нагрузку по всем классам': 'all_load',
    'нагрузка всех классов': 'all_load', 'нагрузка по всем классам': 'all_load',
    'подключись к классу': 'join_class', 'войти в класс': 'join_class',
    'создай класс': 'create_class', 'добавь класс': 'create_class',
    'мой предмет': 'profile', 'измени мой предмет': 'profile_subject',
    'обнови коды': 'rotate_codes', 'обнови коды класса': 'rotate_codes',
    'быстрое дз': 'quick', 'быстрый ввод': 'quick', 'повтори последнее задание': 'repeat',
    'добавь задание': 'add', 'добавь дз': 'add',
}
LESSON_NAMES = {'понедельнику': 'понедельник', 'вторнику': 'вторник', 'среде': 'среда',
                'четвергу': 'четверг', 'пятнице': 'пятница', 'субботе': 'суббота',
                'воскресенью': 'воскресенье'}


def text_field(command, key, limit):
    value = command.get(key)
    if not isinstance(value, str):
        return ''
    value = value.strip()
    return value if 1 <= len(value) <= limit else ''


def time_field(command, minimum=1):
    value = command.get('minutes')
    return value if type(value) is int and minimum <= value <= 600 else None


def local_command(text):
    plain = text.casefold().rstrip('?.!').removeprefix('покажи ').removeprefix('открой ').strip()
    if plain in SHORTCUTS:
        return {'action': SHORTCUTS[plain]}
    match = re.fullmatch(rf'(?:(?:переключись|перейди)\s+(?:на|в)\s+|класс\s+)?({CLASS_LABEL})', plain)
    if match:
        return {'action': 'switch_class', 'class_name': match[1]}
    match = re.fullmatch(rf'(.+?)\s+(?:(?:для|в)\s+)?({CLASS_LABEL})', plain)
    if match and match[1] in SHORTCUTS:
        return {'action': SHORTCUTS[match[1]], 'class_name': match[2]}
    match = re.fullmatch(rf'(?:измени|установи|поставь)\s+порог(?:\s+класса)?'
                         rf'(?:\s+({CLASS_LABEL}))?\s+(?:на\s+)?(\d{{1,3}})(?:\s+мин(?:ут)?)?', plain)
    if match:
        return {'action': 'limit', 'class_name': match[1], 'minutes': int(match[2])}
    match = re.fullmatch(r'(?:измени|поменяй)\s+(?:мой\s+)?предмет\s+на\s+(.+)', text, re.IGNORECASE)
    if match:
        return {'action': 'profile_subject', 'subject': match[1].strip(' «»"')}
    match = re.fullmatch(r'(?:мой )?план на (сегодня|завтра|послезавтра)', plain)
    if match:
        return {'action': 'plan', 'date': match[1]}
    match = ADD_SUBJECT.fullmatch(text)
    if match:
        return {'action': 'class_subject', 'subject': match['subject'].strip(' «»"'),
                'class_name': match['before'] or match['after']}
    header, separator, body = text.partition(':')
    match = ADD_HEADER.fullmatch(header.strip()) if separator and body.strip() else None
    if not match:
        return None
    subject = match['subject']
    duration = MINUTES.search(subject)
    minutes = None
    if duration:
        minutes = int(duration[1]) if duration[2] else int(duration[1]) * 60 + int(duration[3] or 0)
        subject = subject[:duration.start()] + ' ' + subject[duration.end():]
    lesson = LESSON.search(subject)
    if lesson:
        subject = subject[:lesson.start()]
    subject = subject.strip()
    return {'action': 'add', 'class_name': match['class_name'],
            'subject': SUBJECT_NAMES.get(subject.lower(), subject[:1].upper() + subject[1:]),
            'body': body.strip(), 'date': lesson[1].strip() if lesson else None, 'minutes': minutes}


class AgentMixin:
    def menu(self, uid, prefix=''):
        messages = super().menu(uid, prefix)
        member = self.store.member(uid)
        if member and member['role'] != 'student':
            messages[0]['attachments'][0]['payload']['buttons'].append([button('Mera Agent', ENTRY)])
        return messages

    def plan(self, uid, offset=0, page=0):
        messages = super().plan(uid, offset, page)
        messages[0]['attachments'][0]['payload']['buttons'][-1].append(button('Mera Agent', ENTRY))
        return messages

    def route_input(self, uid, text, is_callback):
        lower = text.casefold()
        if lower == ENTRY or is_callback is not True and lower in ALIASES:
            return self.agent_prompt(uid)
        if is_callback is True and text.startswith('agent_edit:'):
            return self.agent_edit_choice(uid, text)
        if is_callback is not True:
            if lower.startswith('/agent '):
                return self.agent_command(uid, text.split(maxsplit=1)[1])
            if lower.startswith(('mera agent,', 'мера агент,')):
                return self.agent_command(uid, text.split(',', 1)[1].strip())
        state = self.store.state(uid)
        if state.get('agent_draft') and state.get('step') in ('subject', 'body') and not state.get('editing_field'):
            if state['step'] == 'subject' and text.startswith('subject:'):
                return self.agent_fill(uid, state, text.split(':', 1)[1])
            if is_callback is not True and text and not text.startswith(('/', 'catalog:')) and text not in (
                    'back', 'menu', 'add', 'load', 'plan', 'tasks', 'manage', 'help', 'profile'):
                return self.agent_fill(uid, state, text)
        if is_callback is not True:
            if state.get('step') == 'agent' and not text.startswith('/'):
                return self.agent_command(uid, text)
            if not state.get('step') and self.store.member(uid):
                command = local_command(text)
                if command or re.match(r'^(?:добавь|задай|создай)(?:те)?\b.{0,50}\b(?:дз|задание|домашку)\b', lower):
                    return self.agent_command(uid, text, command)
        return super().route_input(uid, text, is_callback)

    def agent_prompt(self, uid, error=''):
        member = self.require(uid)
        self.store.set_state(uid, {'step': 'agent'})
        if member['role'] == 'student':
            example = '«Покажи план на завтра» или «Отметь задание по алгебре как выполненное».'
            first = [button('Мой план', 'plan'), button('Моё время', 'free')]
        else:
            example = f'«Покажи задания {member["name"]}» или «Добавь классу предмет Робототехника».'
            first = [button('Добавить задание', 'add'), button('Задания', 'tasks')]
        message = reply(f'{error}Mera Agent · {member["name"]}\n\n{example}\n'
                        'Пишите команды без имён, контактов и кодов доступа.',
                        [first, [button('Нагрузка', 'load'), button('Мои классы', 'classes:0')],
                         [button('Личный кабинет', 'profile')], [button('Меню', 'menu')]])
        message['_visual'] = {'kind': 'help', 'title': 'MERA\nAGENT', 'subtitle': 'Напишите, что нужно сделать',
                              'class_name': member['name'], 'role': ROLE_LABELS[member['role']].capitalize()}
        return [message]

    def agent_command(self, uid, text, command=None):
        member = self.require(uid)
        if len(text) > 900:
            return self.agent_prompt(uid, 'Сократите команду до 900 символов.\n\n')
        if PRIVATE.search(text):
            return self.agent_prompt(uid, 'Уберите контакты и коды доступа из команды.\n\n')
        explicit_target = ADD_TARGET.match(text)
        memberships = self.store.memberships(uid)
        if explicit_target and not any(class_key(row['name']) == class_key(explicit_target[1]) for row in memberships):
            return self.agent_class_error(uid, memberships)
        local = command or local_command(text)
        command = local
        if command is None and self.command_parser and text:
            command = self.command_parser(text)
        if not isinstance(command, dict):
            command = {}
        action = text_field(command, 'action', 20)
        target = explicit_target[1] if explicit_target else text_field(command, 'class_name', 40)
        if target and action not in ('join_class', 'create_class'):
            matches = [row for row in memberships if class_key(row['name']) == class_key(target)]
            if not matches:
                return self.agent_class_error(uid, memberships)
            if len(matches) > 1:
                messages = self.classes_view(uid)
                messages[0]['text'] = 'Есть несколько классов с таким названием. Выберите нужный.\n\n' + messages[0]['text']
                return messages
            if matches[0]['class_id'] != member['class_id']:
                member = self.store.activate_class(uid, matches[0]['class_id'])
        if action == 'switch_class':
            return self.agent_prompt(uid, f'Открыт класс {member["name"]}.\n\n') if target else self.classes_view(uid)
        if action == 'classes':
            return self.classes_view(uid)
        if action == 'all_load':
            return self.class_load_view(uid)
        if action in ('join_class', 'create_class'):
            route = 'classes:join' if action == 'join_class' else 'enroll:head'
            if action == 'create_class' and target:
                return self.create_class(uid, target)
            return self._handle(uid, route)
        if action == 'class_subjects':
            return self.class_subject_view(uid, 0)
        if action == 'class_subject':
            name = text_field(command, 'subject', 60)
            if local or name and name.casefold() in text.casefold():
                return self.add_class_subject(uid, name)
            return self.class_subject_prompt(uid)
        if action == 'profile_subject':
            messages = self._handle(uid, 'profile_subject')
            name = text_field(command, 'subject', 60)
            return self.accept_teacher_subject(uid, name) if name and name.casefold() in text.casefold() else messages
        if action == 'rotate_codes':
            return self._handle(uid, f'rotate_class:{member["class_id"]}')
        if action == 'limit':
            self.require(uid, ('admin',))
            minutes = time_field(command, minimum=30)
            if minutes is None:
                return self._handle(uid, 'limit')
            return [reply(f'{member["name"]} · порог {minutes} мин/день. Сохранить?',
                          [[button('Сохранить', f'limit_class:{member["class_id"]}:{minutes}')],
                           [button('Отмена', 'manage')]])]
        if action == 'add':
            self.require(uid, ('teacher', 'admin'))
            draft = {key: value for key, value in (
                ('subject', text_field(command, 'subject', 60) or member['subject']),
                ('body', text_field(command, 'body', 700)),
                ('minutes', time_field(command)),
            ) if value}
            lesson = self.agent_date(command)
            if lesson and lesson > self.today():
                draft.update(lesson_due=lesson.isoformat(), due=(lesson - timedelta(days=1)).isoformat())
            return self.agent_next_field(uid, {'draft': draft, 'agent_draft': True, 'deadline_mode': 'lesson'})
        if action in TASK_ACTIONS:
            return self.agent_task(uid, member, action, command)
        if action == 'profile':
            messages = self.profile(uid)
            self.store.set_state(uid, {'step': 'agent'})
            return messages
        if action in ('availability', 'today_time'):
            self.require(uid, ('student',))
            minutes = time_field(command, minimum=0)
            weekday = command.get('weekday')
            valid_weekday = type(weekday) is int and 0 <= weekday <= 6
            if action == 'availability' and valid_weekday and minutes is None:
                self.store.set_state(uid, {})
                return self._handle(uid, f'free_day:{weekday}')
            if minutes is not None and (action == 'today_time' or valid_weekday):
                self.store.set_state(uid, {})
                payload = f'today_value:{minutes}' if action == 'today_time' else f'free_value:{weekday}:{minutes}'
                label = 'Сегодня' if action == 'today_time' else WEEKDAYS[weekday]
                return [reply(f'{label} · {minutes} мин на домашние задания.', [[button('Сохранить', payload),
                                                                       button('К агенту', ENTRY)]])]
        if action in VIEWS or action == 'plan':
            route = VIEWS.get(action, 'plan')
            if action == 'tasks' and member['role'] == 'student':
                route = 'plan'
            if action == 'plan':
                day = self.agent_date(command)
                offset = (day - self.today()).days if day else 0
                if offset > 6:
                    return self.agent_prompt(uid, 'В личном плане доступны ближайшие 7 дней.\n\n')
                route = f'plan:{offset}:0'
            self.store.set_state(uid, {})
            messages = self._handle(uid, route)
            if action in ('load', 'tasks', 'plan', 'completed', 'manage', 'part'):
                self.store.set_state(uid, {'step': 'agent'})
            for message in messages:
                message.setdefault('_visual', self.visual(uid, route, message))
            return messages
        return self.agent_prompt(uid, 'Не понял команду. Напишите одно действие.\n\n')

    def agent_class_error(self, uid, memberships):
        if len(memberships) == 1:
            return self.agent_prompt(uid, f'В этом аккаунте доступен класс {memberships[0]["name"]}.\n\n')
        return self.agent_prompt(uid, 'Запрошенный класс не подключён. Откройте «Мои классы».\n\n')

    def agent_date(self, command, key='date'):
        value = text_field(command, key, 24)
        if value:
            value = value.lower().removeprefix('к ').removeprefix('до ')
            try:
                return parse_date(LESSON_NAMES.get(value, value), self.today())
            except ValueError:
                pass
        return None

    def agent_fill(self, uid, state, text):
        self.require(uid, ('teacher', 'admin'))
        step = state['step']
        limit = 60 if step == 'subject' else 700
        if not 1 <= len(text) <= limit:
            raise ValueError(f'Введите от 1 до {limit} символов.')
        state['draft'][step] = text
        return self.agent_next_field(uid, state)

    def agent_next_field(self, uid, state):
        draft = state['draft']
        step = next((key for key in ('subject', 'body', 'due', 'minutes') if key not in draft), 'confirm')
        state['step'] = step
        self.store.set_state(uid, state)
        if step == 'due':
            return self.date_picker(uid)
        if step == 'minutes':
            return self.minute_picker(uid)
        if step == 'confirm':
            return self.preview(uid, draft)
        if step == 'subject':
            return self.subject_picker(uid)
        return [reply('Что нужно сделать?', [[button('Отмена', 'menu')]])]

    def agent_task(self, uid, member, action, command):
        teacher_action = action in ('edit', 'delete')
        self.require(uid, ('admin', 'teacher') if teacher_action else ('student',))
        subject = text_field(command, 'subject', 60).casefold()
        query = text_field(command, 'query', 100).casefold()
        date_filter = text_field(command, 'match_date', 24)
        day = self.agent_date(command, 'match_date')
        if date_filter and not day:
            return self.agent_prompt(uid, 'Уточните дату задания.\n\n')
        tasks = self.store.tasks(member['class_id'])
        done = {row['task_id'] for row in self.db.execute('SELECT task_id FROM done WHERE user_id=?', (uid,))}
        matches = [task for task in tasks
                   if (not teacher_action or member['role'] == 'admin' or task['creator'] == uid)
                   and (teacher_action or ((task['id'] in done) == (action != 'done')))
                   and (not subject or subject in task['subject'].casefold())
                   and (not query or query in task['body'].casefold())
                   and (not day or day.isoformat() == (task['lesson_due'] or task['due']))]
        if not matches:
            return self.agent_prompt(uid, 'Не нашёл подходящее задание. Уточните предмет или текст.\n\n')
        if len(matches) > 5:
            return self.agent_prompt(uid, 'Подходит много заданий. Уточните предмет, текст или дату.\n\n')
        self.store.set_state(uid, {})
        task = matches[0]
        if len(matches) == 1 and action == 'edit':
            return self.agent_edit(uid, task, command)
        if len(matches) == 1 and action == 'delete':
            route = 'delete:' + task['id']
            messages = self._handle(uid, route)
            messages[0]['_visual'] = self.visual(uid, route, messages[0])
            return messages
        prefix = 'actual' if action == 'actual' and time_field(command) else 'actual_custom' if action == 'actual' else action
        suffix = f':{time_field(command)}' if prefix == 'actual' else ''
        if action == 'edit':
            nonce = secrets.token_hex(5)
            prefix = f'agent_edit:{nonce}'
            self.store.set_state(uid, {'agent_choice': {'nonce': nonce, 'ids': [item['id'] for item in matches],
                                                        'changes': {key: command[key] for key in
                                                                    ('new_subject', 'body', 'date', 'minutes')
                                                                    if key in command}}})
        label = {'edit': 'Изменить', 'delete': 'Удалить', 'done': 'Отметить выполненным',
                 'undo': 'Отменить отметку', 'actual': 'Указать время'}[action]
        rows = [[button(f'{task["subject"]} · {task["body"][:27]}', f'{prefix}:{task["id"]}{suffix}')]
                for task in matches]
        if len(matches) == 1:
            rows[0][0]['text'] = label
            prompt = f'{task["subject"]} · {task["body"]}\n{self.deadline(task)}'
            if suffix:
                prompt += f'\nФактическое время — {time_field(command)} мин.'
        else:
            prompt = f'{label}. Какое задание?'
        rows.append([button('К агенту', ENTRY)])
        message = reply(prompt, rows)
        message['_visual'] = {'kind': 'tasks', 'title': 'ВЫБЕРИТЕ\nЗАДАНИЕ' if len(matches) > 1 else 'ПРОВЕРЬТЕ\nЗАДАНИЕ',
                              'subtitle': label, 'class_name': member['name'], 'count': len(matches),
                              'total': sum(task['minutes'] for task in matches),
                              'entries': [{'subject': task['subject'], 'body': task['body'],
                                           'minutes': task['minutes'], 'due': self.deadline(task)}
                                          for task in matches[:3]]}
        return [message]

    def agent_edit_choice(self, uid, text):
        _, _, choice = text.partition(':')
        nonce, _, task_id = choice.partition(':')
        saved = self.store.state(uid).get('agent_choice') or {}
        if nonce != saved.get('nonce') or task_id not in saved.get('ids', ()):
            raise ValueError('Выбор устарел. Напишите команду заново.')
        task = dict(self.owned_task(uid, task_id))
        return self.agent_edit(uid, task, saved['changes'])

    def agent_edit(self, uid, task, command):
        draft = {key: task[key] for key in ('subject', 'body', 'due', 'minutes', 'lesson_due')}
        for key, value in (('subject', text_field(command, 'new_subject', 60)),
                           ('body', text_field(command, 'body', 700)), ('minutes', time_field(command))):
            if value:
                draft[key] = value
        if text_field(command, 'date', 24):
            lesson = self.agent_date(command)
            if not lesson or lesson <= self.today():
                return self.agent_prompt(uid, 'Укажите день урока начиная с завтрашнего.\n\n')
            draft.update(lesson_due=lesson.isoformat(), due=(lesson - timedelta(days=1)).isoformat())
        if all(draft[key] == task[key] for key in draft):
            return self.edit_menu(uid, task['id'])
        self.store.set_state(uid, {'agent_draft': True, 'edit_id': task['id'], 'base_revision': task['revision'],
                                   'deadline_mode': 'lesson' if draft['lesson_due'] else 'evening'})
        return self.preview(uid, draft)
