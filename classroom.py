"""Вход по ролям, выбор предмета и личный кабинет класса."""
from mera import ROLE_LABELS, bounded_int, button, reply
from subjects import SUBJECT_GROUPS, subject_name


SUBJECT_STEPS = ('teacher_subject', 'profile_subject', 'subject')
LETTERS = ('А', 'Б', 'В', 'Г', 'Д')
PAGE_SIZE = 7


def card(text, rows, title, subtitle, **data):
    message = reply(text, rows)
    message['_visual'] = {'kind': 'menu', 'title': title, 'subtitle': subtitle, **data}
    return [message]


def pages(page, count, payload):
    row = []
    if page:
        row.append(button('‹ Назад', f'{payload}:{page - 1}'))
    if (page + 1) * PAGE_SIZE < count:
        row.append(button('Дальше ›', f'{payload}:{page + 1}'))
    return [row] if row else []


class ClassroomMixin:
    def menu(self, uid, prefix=''):
        member = self.store.member(uid)
        if not member:
            return card(prefix + 'МЕРА · выберите свою роль\n\n'
                        'Классный руководитель создаёт класс. Учитель и ученик подключаются по коду.',
                        [[button('Классный руководитель', 'enroll:head')],
                         [button('Учитель', 'enroll:teacher')], [button('Ученик', 'enroll:student')]],
                        'ВЫБЕРИТЕ\nРОЛЬ', 'Начнём с класса')
        messages = super().menu(uid, prefix)
        if member['role'] != 'student':
            messages[0]['attachments'][0]['payload']['buttons'].append([button('Личный кабинет', 'profile')])
        return messages

    def plan(self, uid, offset=0, page=0):
        messages = super().plan(uid, offset, page)
        rows = messages[0]['attachments'][0]['payload']['buttons']
        rows.insert(-1, [button('Личный кабинет', 'profile')])
        return messages

    def route_input(self, uid, text, is_callback):
        step = self.store.state(uid).get('step')
        if (step in ('teacher_subject', 'profile_subject', 'class_subject_add') and is_callback is not True and
                not text.startswith(('/', 'subject:', 'catalog:', 'enroll:')) and
                text not in ('menu', 'profile', 'help')):
            if step == 'class_subject_add':
                return self.add_class_subject(uid, text)
            return self.accept_teacher_subject(uid, text)
        return super().route_input(uid, text, is_callback)

    def _handle(self, uid, text):
        state = self.store.state(uid)
        if text in ('enroll:head', 'enroll:teacher', 'enroll:student'):
            if self.store.member(uid):
                return self.profile(uid)
            if text == 'enroll:head':
                self.store.set_state(uid, {'step': 'create'})
                return card('Выберите номер класса или напишите название, например 9А.',
                            [[button(str(grade), f'class_grade:{grade}') for grade in grades]
                             for grades in (range(1, 5), range(5, 9), range(9, 12))] +
                            [[button('Назад к ролям', 'menu')]],
                            'СОЗДАЙТЕ\nКЛАСС', 'Выберите параллель или введите название')
            if text == 'enroll:teacher':
                self.store.set_state(uid, {'step': 'teacher_subject'})
                return self.subject_picker(uid)
            state = {'step': 'join', 'expected_role': 'student'}
            self.store.set_state(uid, state)
            return self.invite_prompt(state)
        if text.startswith(('class_grade:', 'class_pick:')):
            if state.get('step') != 'create' or self.store.member(uid):
                raise ValueError('Откройте создание класса заново через меню.')
            action, _, argument = text.partition(':')
            if action == 'class_grade':
                grade = bounded_int(argument, 1, 11)
                self.store.set_state(uid, {'step': 'create', 'grade': grade})
                return card(f'{grade} класс. Выберите букву или напишите полное название.',
                            [[button(letter, f'class_pick:{grade}:{letter}') for letter in LETTERS],
                             [button('Назад', 'enroll:head')]], 'СОЗДАЙТЕ\nКЛАСС', f'Выбрана параллель {grade}')
            grade, _, letter = argument.partition(':')
            if grade != str(state.get('grade')) or letter not in LETTERS:
                raise ValueError('Выберите букву в актуальной карточке.')
            return super()._handle(uid, grade + letter)
        if text.startswith('catalog:'):
            return self.subject_picker(uid, text.split(':', 1)[1])
        if text.startswith('subject:') and state.get('step') in ('teacher_subject', 'profile_subject'):
            return self.accept_teacher_subject(uid, text.split(':', 1)[1])
        if text in ('profile', '/profile'):
            return self.profile(uid)
        if text == 'profile_subject':
            member = self.require(uid, ('teacher',))
            if member['subject']:
                return self.profile(uid)
            self.store.set_state(uid, {'step': 'profile_subject'})
            return self.subject_picker(uid)
        if text == 'class_subject_add':
            return self.class_subject_prompt(uid)
        if text.startswith('class_subjects:'):
            return self.class_subject_view(uid, bounded_int(text.split(':', 1)[1], 0, 999))
        return super()._handle(uid, text)

    def invite_prompt(self, state, error=''):
        teacher = state['expected_role'] == 'teacher'
        lines = [error, f'Предмет — {state["teacher_subject"]}.' if teacher else '',
                 'Введите код для учителя.' if teacher else 'Введите код для ученика.',
                 'Код выдаёт классный руководитель.']
        return card('\n'.join(line for line in lines if line),
                    [[button('Изменить предмет' if teacher else 'Назад к ролям',
                             'enroll:teacher' if teacher else 'menu')]],
                    'ВХОД\nВ КЛАСС', 'Подключение по приглашению',
                    subject=state.get('teacher_subject'))

    def accept_teacher_subject(self, uid, name):
        state = self.store.state(uid)
        name = subject_name(name)
        if not name:
            return self.subject_picker(uid, error='Введите название предмета от 1 до 60 символов.')
        if state.get('step') == 'teacher_subject' and not self.store.member(uid):
            state = {'step': 'join', 'expected_role': 'teacher', 'teacher_subject': name}
            self.store.set_state(uid, state)
            return self.invite_prompt(state)
        if state.get('step') == 'profile_subject':
            member = self.require(uid, ('teacher',))
            self.store.set_teacher_subject(uid, member['class_id'], name)
            return self.profile(uid, prefix='Предмет добавлен.\n\n')
        raise ValueError('Откройте выбор предмета заново.')

    def subject_picker(self, uid, screen='base', error=''):
        state = self.store.state(uid)
        step = state.get('step')
        if step not in SUBJECT_STEPS:
            raise ValueError('Откройте выбор предмета заново.')
        member = self.store.member(uid)
        onboarding = step == 'teacher_subject'
        if onboarding and member:
            return self.profile(uid)
        if not onboarding:
            self.require(uid, ('admin', 'teacher') if step == 'subject' else ('teacher',))
        target = 'menu' if onboarding or step == 'subject' else 'profile'
        prompt = 'Какой предмет вы ведёте?' if step != 'subject' else 'По какому предмету задание?'
        rows = []
        if screen == 'base':
            if member and member['subject'] and step == 'subject':
                name = member['subject']
                rows.append([button(name, f'subject:{name}')])
            if member and self.store.class_subjects(member['class_id']):
                rows.append([button('Предметы класса', 'catalog:class:0')])
            rows.extend([button(label, f'catalog:{key}:0')] for key, (label, _) in SUBJECT_GROUPS.items())
            rows.append([button('Свой предмет', 'catalog:custom')])
            rows.append([button('Отмена', target)])
            text = prompt + '\nВыберите ступень или сразу напишите название.'
        elif screen == 'custom':
            text = 'Напишите название предмета, курса или модуля. Например, «Робототехника».'
            rows = [[button('Назад к списку', 'catalog:base')], [button('Отмена', target)]]
        else:
            group, _, page_text = screen.partition(':')
            page = bounded_int(page_text, 0, 999)
            if group == 'class' and member:
                label = 'Предметы класса'
                names = [row['name'] for row in self.store.class_subjects(member['class_id'])]
            elif group in SUBJECT_GROUPS:
                label, names = SUBJECT_GROUPS[group]
            else:
                raise ValueError('Откройте список предметов заново.')
            page = min(page, max(0, (len(names) - 1) // PAGE_SIZE))
            rows = [[button(name, f'subject:{name}')] for name in names[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
            rows.extend(pages(page, len(names), f'catalog:{group}'))
            if group == 'upper':
                rows.append([button('Профильный курс', 'catalog:custom')])
            rows.append([button('Назад к ступеням', 'catalog:base')])
            text = f'{label} · выберите предмет или напишите свой.'
        if error:
            text = error + '\n\n' + text
        if step == 'subject':
            return [reply(text, rows)]
        return card(text, rows, 'ВЫБЕРИТЕ\nПРЕДМЕТ', 'Предмет можно вписать вручную')

    def profile(self, uid, prefix=''):
        member = self.require(uid)
        self.store.set_state(uid, {})
        role = member['role']
        lines = [prefix + f'Личный кабинет · {member["name"]}', f'Роль — {ROLE_LABELS[role]}.']
        rows = []
        if role == 'teacher':
            lines.append(f'Мой предмет — {member["subject"] or "пока не выбран"}.')
            if not member['subject']:
                rows.append([button('Выбрать предмет', 'profile_subject')])
        if role == 'student':
            rows.extend([[button('Мой план', 'plan'), button('Моё время', 'free')],
                         [button('Выполнено', 'completed')]])
        else:
            rows.append([button('Задания и статистика', 'tasks')])
        rows.append([button('Предметы класса', 'class_subjects:0')])
        if role == 'admin':
            rows.append([button('Управление классом', 'manage')])
        rows.extend([[button('Mera Agent', 'mera_agent')], [button('Главное меню', 'menu')]])
        return card('\n'.join(lines), rows, 'ЛИЧНЫЙ\nКАБИНЕТ', 'Ваш класс и настройки',
                    class_name=member['name'], role=ROLE_LABELS[role].capitalize(), subject=member['subject'])

    def class_subject_prompt(self, uid, error=''):
        member = self.require(uid, ('admin', 'teacher'))
        self.store.set_state(uid, {'step': 'class_subject_add'})
        text = 'Напишите название предмета для класса. Например, «Робототехника».'
        return card(f'{error}\n\n{text}' if error else text,
                    [[button('К предметам класса', 'class_subjects:0')]],
                    'НОВЫЙ\nПРЕДМЕТ', f'Класс {member["name"]}', class_name=member['name'])

    def add_class_subject(self, uid, name):
        member = self.require(uid, ('admin', 'teacher'))
        name = subject_name(name)
        if not name:
            return self.class_subject_prompt(uid, 'Введите название от 1 до 60 символов.')
        self.store.ensure_class_subject(member['class_id'], name)
        return self.class_subject_view(uid, 0, prefix=f'Предмет «{name}» в списке класса.\n\n')

    def class_subject_view(self, uid, page, prefix=''):
        member = self.require(uid)
        self.store.set_state(uid, {})
        subjects = self.store.class_subjects(member['class_id'])
        last_page = max(0, (len(subjects) - 1) // PAGE_SIZE)
        page = min(page, last_page)
        lines = [prefix + f'Предметы класса {member["name"]} · {len(subjects)}']
        for subject in subjects[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            line = f'• {subject["name"]}'
            if member['role'] == 'admin':
                line += f' · учителей: {subject["teachers"]}'
            lines.append(line)
        if not subjects:
            lines.append('Список пополнится, когда учитель подключится или появится первое задание.')
        rows = pages(page, len(subjects), 'class_subjects')
        if member['role'] in ('admin', 'teacher'):
            rows.append([button('Добавить предмет', 'class_subject_add')])
        rows.append([button('В личный кабинет', 'profile')])
        return card('\n'.join(lines), rows, 'ПРЕДМЕТЫ\nКЛАССА', f'В списке — {len(subjects)}',
                    class_name=member['name'])
