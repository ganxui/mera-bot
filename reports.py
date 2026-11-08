"""Class head reports built from student memberships and task completion marks."""
from classroom import PAGE_SIZE, card, pages
from mera import bounded_int, button


TASK_PAGE_SIZE = 4
MAX_ID = 2**63 - 1


def report_page(value):
    return bounded_int(value, 0, 999)


def assignment_caption(task, body_limit=85):
    body = ' '.join(task['body'].split())
    if len(body) > body_limit:
        body = body[:body_limit - 1].rstrip() + '…'
    return f'{task["subject"]} · {body}'


class StudentReportsMixin:
    def route_input(self, uid, text, is_callback):
        state = self.store.state(uid)
        if state.get('step') == 'student_display_name' and is_callback is not True and not text.startswith('/'):
            member = self.require(uid, ('admin',))
            if member['class_id'] != state['class_id']:
                raise ValueError('Откройте карточку ученика в нужном классе.')
            student = self.report_student(member['class_id'], state['student_id'])
            name = ' '.join(text.split())
            if not 1 <= len(name) <= 60 or not name.isprintable() or not any(char.isalpha() for char in name):
                return self.student_name_prompt(uid, member, student, 'Введите имя от 1 до 60 символов.')
            self.db.execute('UPDATE student_records SET display_name=? WHERE id=? AND class_id=?',
                            (name, student['id'], member['class_id']))
            return self.student_report(uid, student['id'], 'done', 0, prefix='Имя сохранено.\n\n')
        return super().route_input(uid, text, is_callback)

    def _handle(self, uid, text):
        if text.startswith('students:'):
            return self.student_roster(uid, report_page(text.split(':', 1)[1]))
        if text.startswith('student_tasks:'):
            return self.class_task_report(uid, report_page(text.split(':', 1)[1]))
        if text.startswith('student_report:'):
            parts = text.split(':')
            if len(parts) != 4 or parts[2] not in ('done', 'pending'):
                raise ValueError('Откройте список учеников заново.')
            return self.student_report(uid, bounded_int(parts[1], 1, MAX_ID), parts[2], report_page(parts[3]))
        if text.startswith('student_name:'):
            member = self.require(uid, ('admin',))
            student = self.report_student(member['class_id'], bounded_int(text.split(':', 1)[1], 1, MAX_ID))
            return self.student_name_prompt(uid, member, student)
        return super()._handle(uid, text)

    def roster_count(self, class_id):
        return self.db.execute("SELECT COUNT(*) FROM class_memberships WHERE class_id=? AND role='student'",
                               (class_id,)).fetchone()[0]

    def assignment_count(self, class_id):
        return self.db.execute('SELECT COUNT(*) FROM tasks WHERE class_id=?', (class_id,)).fetchone()[0]

    def class_assignment_rows(self, class_id, size, offset):
        return self.db.execute('''SELECT t.subject,t.body,t.due,td.lesson_due,
            (SELECT COUNT(*) FROM done d JOIN class_memberships m ON m.user_id=d.user_id
             WHERE m.class_id=t.class_id AND m.role='student' AND d.task_id=t.id) AS completed
            FROM tasks t LEFT JOIN task_details td ON td.task_id=t.id
            WHERE t.class_id=? ORDER BY t.rowid DESC LIMIT ? OFFSET ?''', (class_id, size, offset)).fetchall()

    def student_roster(self, uid, page=0):
        member = self.require(uid, ('admin',))
        self.store.set_state(uid, {})
        class_id = member['class_id']
        student_count = self.roster_count(class_id)
        task_count = self.assignment_count(class_id)
        page = min(page, max(0, (student_count - 1) // PAGE_SIZE))
        offset = page * PAGE_SIZE
        students = self.db.execute('''SELECT r.id,r.display_name,
            (SELECT COUNT(*) FROM done d JOIN tasks t ON t.id=d.task_id
             WHERE d.user_id=r.user_id AND t.class_id=r.class_id) AS completed
            FROM student_records r JOIN class_memberships m ON m.user_id=r.user_id AND m.class_id=r.class_id
            WHERE r.class_id=? AND m.role='student' ORDER BY r.id LIMIT ? OFFSET ?''',
            (class_id, PAGE_SIZE, offset)).fetchall()
        lines = [f'Ученики · {member["name"]}', f'Ученики — {student_count}. Задания — {task_count}.']
        latest = self.class_assignment_rows(class_id, 1, 0)
        if latest:
            task = latest[0]
            lines.extend(['', f'Последнее задание · {assignment_caption(task)}',
                          f'Выполнили — {task["completed"]}. Без отметки — {student_count - task["completed"]}.'])
        lines.append('')
        rows = []
        for number, student in enumerate(students, offset + 1):
            name = student['display_name'] or f'Ученик {number}'
            lines.append(f'{name} · выполнено {student["completed"]} из {task_count}')
            rows.append([button(name, f'student_report:{student["id"]}:done:0')])
        if not students:
            lines.append('Ученики ещё не подключились к классу.')
        rows.extend(pages(page, student_count, 'students'))
        rows.extend([[button('Отчёт по заданиям', 'student_tasks:0')], [button('Управление классом', 'manage')]])
        return card('\n'.join(lines), rows, 'УЧЕНИКИ\nКЛАССА', 'Выберите ученика для просмотра',
                    kind='report', class_name=member['name'],
                    metrics=[('УЧЕНИКОВ', student_count), ('ЗАДАНИЙ', task_count)])

    def class_task_report(self, uid, page=0):
        member = self.require(uid, ('admin',))
        self.store.set_state(uid, {})
        class_id = member['class_id']
        student_count = self.roster_count(class_id)
        task_count = self.assignment_count(class_id)
        page = min(page, max(0, (task_count - 1) // TASK_PAGE_SIZE))
        tasks = self.class_assignment_rows(class_id, TASK_PAGE_SIZE, page * TASK_PAGE_SIZE)
        lines = [f'Отчёт по заданиям · {member["name"]}', f'Подключено учеников — {student_count}.']
        for task in tasks:
            lines.extend(['', assignment_caption(task), self.deadline(task),
                          f'Выполнили — {task["completed"]}. Без отметки — {student_count - task["completed"]}.'])
        if not tasks:
            lines.append('\nВ классе пока нет заданий.')
        rows = pages(page, task_count, 'student_tasks', TASK_PAGE_SIZE)
        rows.append([button('К списку учеников', 'students:0')])
        metrics = [('ВЫПОЛНИЛИ', tasks[0]['completed']), ('БЕЗ ОТМЕТКИ', student_count - tasks[0]['completed'])] if tasks else []
        return card('\n'.join(lines), rows, 'ОТЧЁТ ПО\nЗАДАНИЯМ', assignment_caption(tasks[0]) if tasks else 'Заданий пока нет',
                    kind='report', class_name=member['name'], metrics=metrics)

    def report_student(self, class_id, student_id):
        row = self.db.execute('''SELECT r.*,
            (SELECT COUNT(*) FROM student_records preceding
             WHERE preceding.class_id=r.class_id AND preceding.id<=r.id) AS number,
            (SELECT COUNT(*) FROM done d JOIN tasks t ON t.id=d.task_id
             WHERE d.user_id=r.user_id AND t.class_id=r.class_id) AS completed
            FROM student_records r JOIN class_memberships m ON m.user_id=r.user_id AND m.class_id=r.class_id
            WHERE r.class_id=? AND r.id=? AND m.role='student' ''', (class_id, student_id)).fetchone()
        if not row:
            raise ValueError('Ученик не найден в выбранном классе.')
        student = dict(row)
        student['name'] = student['display_name'] or f'Ученик {student["number"]}'
        return student

    def student_report(self, uid, student_id, view, page, prefix=''):
        member = self.require(uid, ('admin',))
        student = self.report_student(member['class_id'], student_id)
        self.store.set_state(uid, {})
        total = self.assignment_count(member['class_id'])
        done = student['completed']
        pending = total - done
        shown_done = view == 'done'
        count = done if shown_done else pending
        page = min(page, max(0, (count - 1) // TASK_PAGE_SIZE))
        tasks = self.db.execute('''SELECT t.subject,t.body,t.due,td.lesson_due
            FROM tasks t LEFT JOIN task_details td ON td.task_id=t.id
            LEFT JOIN done d ON d.task_id=t.id AND d.user_id=?
            WHERE t.class_id=? AND (d.task_id IS NOT NULL)=?
            ORDER BY t.rowid DESC LIMIT ? OFFSET ?''',
            (student['user_id'], member['class_id'], int(shown_done), TASK_PAGE_SIZE, page * TASK_PAGE_SIZE)).fetchall()
        lines = [prefix + f'{student["name"]} · {member["name"]}', f'Выполнено — {done}. Без отметки — {pending}.',
                 '', 'Выполненные задания' if shown_done else 'Задания без отметки о выполнении']
        for task in tasks:
            lines.extend(['', assignment_caption(task, 360), self.deadline(task)])
        if not tasks:
            lines.append('Заданий в этом разделе пока нет.')
        rows = pages(page, count, f'student_report:{student_id}:{view}', TASK_PAGE_SIZE)
        alternate = button(f'Без отметки · {pending}', f'student_report:{student_id}:pending:0') if shown_done else (
            button(f'Выполнено · {done}', f'student_report:{student_id}:done:0'))
        rows.extend([[alternate], [button('Изменить имя', f'student_name:{student_id}')],
                     [button('К списку учеников', 'students:0')]])
        return card('\n'.join(lines), rows, 'ОТЧЁТ ПО\nУЧЕНИКУ', student['name'],
                    kind='report', class_name=member['name'],
                    metrics=[('ВЫПОЛНЕНО', done), ('БЕЗ ОТМЕТКИ', pending)])

    def student_name_prompt(self, uid, member, student, error=''):
        self.store.set_state(uid, {'step': 'student_display_name', 'class_id': member['class_id'],
                                   'student_id': student['id']})
        text = f'Текущее имя — {student["name"]}.\nВведите новое отображаемое имя.'
        return card(f'{error}\n\n{text}' if error else text,
                    [[button('Отмена', f'student_report:{student["id"]}:done:0')]],
                    'ИМЯ\nУЧЕНИКА', student['name'], class_name=member['name'])
