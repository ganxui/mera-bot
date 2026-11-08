"""Повседневные сценарии: сроки к уроку, прогресс, учёт времени и редактирование."""
import calendar
import hashlib
import json
import re
import secrets
from datetime import date, timedelta

from mera import reply, button, parse_date, pretty_date, bounded_int, WEEKDAYS, MONTH_TITLES


class WorkflowMixin:
    def task(self,uid,task_id):
        member=self.require(uid)
        row=self.db.execute('''SELECT t.*,d.lesson_due,COALESCE(d.revision,0) AS revision
            FROM tasks t LEFT JOIN task_details d ON d.task_id=t.id WHERE t.id=? AND t.class_id=?''',
            (task_id,member['class_id'])).fetchone()
        if not row:
            raise ValueError('Задание не найдено в вашем классе или удалено.')
        return row

    def owned_task(self,uid,task_id):
        member=self.require(uid,('admin','teacher'))
        task=self.task(uid,task_id)
        if member['role']!='admin' and task['creator']!=uid:
            raise ValueError('Учитель может изменять только собственные задания.')
        return task

    def deadline(self,task):
        task=dict(task)
        return ('К уроку '+pretty_date(task['lesson_due'])) if task.get('lesson_due') else ('До конца дня '+pretty_date(task['due']))

    def learning_totals(self,uid):
        progress={r['task_id']:r['n'] for r in self.db.execute(
            'SELECT task_id,SUM(credited) AS n FROM study_log WHERE user_id=? AND task_id IS NOT NULL GROUP BY task_id',(uid,))}
        spent={r['day']:r['n'] for r in self.db.execute('SELECT day,SUM(worked) AS n FROM study_log WHERE user_id=? GROUP BY day',(uid,))}
        overrides={r['day']:r['minutes'] for r in self.db.execute('SELECT day,minutes FROM day_capacity WHERE user_id=?',(uid,))}
        return progress,spent,overrides

    def remaining(self,uid,task):
        n=self.db.execute('SELECT COALESCE(SUM(credited),0) FROM study_log WHERE user_id=? AND task_id=?',(uid,task['id'])).fetchone()[0]
        return max(0,task['minutes']-n)

    def part_token(self,uid,task,minutes):
        credited=self.db.execute('SELECT COALESCE(SUM(credited),0) FROM study_log WHERE user_id=? AND task_id=?',(uid,task['id'])).fetchone()[0]
        raw=f'{uid}:{task["id"]}:{self.today()}:{credited}:{minutes}:{task.get("revision",0)}'
        return hashlib.sha256(raw.encode()).hexdigest()[:14]

    def menu(self,uid,prefix=''):
        member=self.store.member(uid)
        if member and member['role']=='student':
            messages=self.plan(uid)
            if prefix:messages[0]['text']=prefix+messages[0]['text']
            return messages
        messages=super().menu(uid,prefix)
        if member and self.db.execute('SELECT 1 FROM teacher_defaults WHERE user_id=?',(uid,)).fetchone():
            rows=messages[0]['attachments'][0]['payload']['buttons']
            rows.insert(1,[button('Быстрое ДЗ','add_quick'),button('Повторить','repeat_last')])
        return messages

    def _handle(self,uid,text):
        state=self.store.state(uid)
        step=state.get('step')
        if text == 'own_minutes' or text.startswith('estimate:'):
            self.require(uid, ('admin', 'teacher'))
            if step != 'minutes':
                raise ValueError('Карточка устарела. Откройте задание заново.')
            if text == 'own_minutes':
                return self.manual_minutes(uid, state)
            estimate = state.get('time_estimate') or {}
            if not estimate.get('suggestion') or text != f'estimate:{estimate.get("token")}':
                raise ValueError('Оценка устарела. Выберите время в текущей карточке.')
            text = f'minutes:{estimate["suggestion"]["minutes"]}'
        if text in ('/help','help'):
            return [reply('МЕРА распределяет добавленные учителями задания.\n\n'
                'Новые задания задаются «к уроку»: выберите день урока, подготовка планируется до предыдущего вечера. '
                'Старые задания сохраняют выбранный ранее срок «до конца дня».\n\n'
                '«Часть готова» учитывает указанную часть плана. «Всё готово» завершает задание целиком. '
                'Учтённые минуты уменьшают остаток времени на день. Фактическое время можно уточнить; '
                'ошибочную отметку — отменить в «Выполнено».\n\n'
                'План учитывает только задания в боте. Порог класса — настройка пилота, а не норматив. '
                'Изменения срока согласовывает учитель; бот не переносит его самостоятельно.',[[button('К плану / меню','menu')]])]
        if text=='completed' or text.startswith('completed:'):
            return self.completed(uid,int(text.split(':')[1]) if ':' in text else 0)
        if text=='today_time':
            self.require(uid,('student',))
            return [reply('Сколько всего времени есть на домашку сегодня?\nУже учтённая работа будет вычтена из этого лимита. '
                'Настройка действует только сегодня.',
                [[button(f'{n} мин' if n else 'Сегодня занят',f'today_value:{n}') for n in pair] for pair in ((0,30),(60,90),(120,180))]+
                [[button('Как обычно','today_value:default')],[button('Назад к плану','plan')]])]
        if text.startswith('today_value:'):
            self.require(uid,('student',))
            value=text.split(':')[1]
            if value=='default':
                self.db.execute('DELETE FROM day_capacity WHERE user_id=? AND day=?',(uid,self.today().isoformat()))
            else:
                self.db.execute('INSERT OR REPLACE INTO day_capacity VALUES (?,?,?)',(uid,self.today().isoformat(),bounded_int(value,0,600)))
            return self.plan(uid)
        if text.startswith('done:'):
            self.require(uid,('student',))
            task=self.task(uid,text.split(':')[1])
            existing=self.db.execute('SELECT 1 FROM done WHERE user_id=? AND task_id=?',(uid,task['id'])).fetchone()
            if existing:return [reply('Это задание уже отмечено выполненным.',[[button('Выполнено','completed'),button('План','plan')]])]
            remaining=self.remaining(uid,task)
            token='complete-'+secrets.token_hex(12)
            log=self.db.execute('INSERT INTO study_log(user_id,task_id,day,worked,credited,token) VALUES (?,?,?,?,?,?)',
                (uid,task['id'],self.today().isoformat(),remaining,remaining,token))
            self.db.execute('INSERT INTO done(user_id,task_id) VALUES (?,?)',(uid,task['id']))
            self.db.execute('INSERT INTO completion_info VALUES (?,?,?,?)',(uid,task['id'],self.today().isoformat(),log.lastrowid))
            return [reply(f'Готово: {task["subject"]}.\n\nСегодня учтено ещё {remaining} мин по оценке задания. '
                'Если время отличалось, укажите фактические минуты за всё задание. Ввод добровольный.',
                [[button(f'{n} мин',f'actual:{task["id"]}:{n}') for n in (15,30,45)],
                 [button('Другое время',f'actual_custom:{task["id"]}'),button('К плану','plan')],
                 [button('Отменить «готово»',f'undo:{task["id"]}')]])]
        if text.startswith('part:'):
            self.require(uid,('student',))
            parts=text.split(':')
            if len(parts)!=4:raise ValueError('Откройте актуальный план.')
            task=dict(self.task(uid,parts[1]));minutes=bounded_int(parts[2],1,600);token=parts[3]
            if self.db.execute('SELECT 1 FROM study_log WHERE token=? AND user_id=?',(token,uid)).fetchone():
                return [reply('Эта часть уже учтена.',[[button('К актуальному плану','plan')]])]
            if self.db.execute('SELECT 1 FROM done WHERE user_id=? AND task_id=?',(uid,task['id'])).fetchone():
                raise ValueError('Всё задание уже выполнено. Откройте «Выполнено».')
            if token!=self.part_token(uid,task,minutes) or minutes>=self.remaining(uid,task):
                raise ValueError('План изменился. Откройте его заново перед отметкой части.')
            self.db.execute('INSERT INTO study_log(user_id,task_id,day,worked,credited,token) VALUES (?,?,?,?,?,?)',
                (uid,task['id'],self.today().isoformat(),minutes,minutes,token))
            result=reply(f'{task["subject"]}: учтено {minutes} мин.\nОсталось по оценке: {self.remaining(uid,task)} мин. '
                'Задание целиком ещё не завершено.',[[button('К плану','plan')],[button('Отменить часть',f'undo_work:{token}')]])
            result['_visual']={'kind':'done','title':'ЧАСТЬ\nГОТОВА','subtitle':f'Осталось {self.remaining(uid,task)} мин по оценке',
                'subject':task['subject'],'body':task['body'],'minutes':minutes,'actual':None,
                'completed':task['minutes']-self.remaining(uid,task),'count':task['minutes'],'progress_unit':'мин'}
            return [result]
        if text.startswith('undo:'):
            self.require(uid,('student',));task=self.task(uid,text.split(':')[1])
            info=self.db.execute('SELECT log_id FROM completion_info WHERE user_id=? AND task_id=?',(uid,task['id'])).fetchone()
            self.db.execute('DELETE FROM completion_info WHERE user_id=? AND task_id=?',(uid,task['id']))
            self.db.execute('DELETE FROM done WHERE user_id=? AND task_id=?',(uid,task['id']))
            if info and info['log_id']:
                self.db.execute('DELETE FROM study_log WHERE id=? AND user_id=?',(info['log_id'],uid))
            return self.plan(uid)
        if text.startswith('undo_work:'):
            self.require(uid,('student',));token=text.split(':')[1]
            row=self.db.execute('SELECT * FROM study_log WHERE token=? AND user_id=?',(token,uid)).fetchone()
            if not row:return self.plan(uid)
            if self.db.execute('SELECT 1 FROM done WHERE user_id=? AND task_id=?',(uid,row['task_id'])).fetchone():
                raise ValueError('Сначала отмените отметку всего задания в «Выполнено».')
            latest=self.db.execute('SELECT MAX(id) FROM study_log WHERE user_id=? AND task_id=?',(uid,row['task_id'])).fetchone()[0]
            if row['id']!=latest:raise ValueError('Можно отменить последнюю учтённую часть задания.')
            self.db.execute('DELETE FROM study_log WHERE id=?',(row['id'],))
            return self.plan(uid)
        if text in ('add_quick','repeat_last'):
            self.require(uid,('admin','teacher'))
            saved=self.db.execute('SELECT * FROM teacher_defaults WHERE user_id=?',(uid,)).fetchone()
            if not saved:raise ValueError('Сначала опубликуйте первое задание обычным способом.')
            draft={'subject':saved['subject'],'minutes':saved['minutes']}
            if self.estimator:
                draft.pop('minutes')
            if text=='repeat_last':draft['body']=saved['body']
            self.store.set_state(uid,{'step':'due' if text=='repeat_last' else 'body','draft':draft,
                'deadline_mode':'lesson','previous_minutes':saved['minutes']})
            if text=='repeat_last':return self.date_picker(uid)
            return [reply(f'{saved["subject"]} · обычно {saved["minutes"]} мин.\nЧто нужно сделать?',
                [[button('Назад','back'),button('Отмена','menu')]])]
        if text.startswith('edit:'):
            return self.edit_menu(uid,text.split(':')[1])
        if text.startswith('edit_field:'):
            _,task_id,field=text.split(':')
            task=dict(self.owned_task(uid,task_id))
            if field not in ('subject','body','due','minutes'):raise ValueError('Неизвестное поле задания.')
            draft={k:task[k] for k in ('subject','body','due','minutes','lesson_due')}
            self.store.set_state(uid,{'step':field,'draft':draft,'edit_id':task_id,'base_revision':task['revision'],
                'editing_field':True,'deadline_mode':'lesson' if task.get('lesson_due') else 'evening'})
            if field=='due':return self.date_picker(uid)
            if field=='minutes':return self.minute_picker(uid)
            return [reply('Введите новый предмет:' if field=='subject' else 'Введите новый текст задания:',
                [[button('Назад',f'edit:{task_id}'),button('Отмена','menu')]])]
        if text.startswith('draft_body:'):
            self.require(uid,('admin','teacher'))
            if step!='confirm' or text.split(':')[1]!=state.get('nonce'):raise ValueError('Карточка устарела. Откройте задание заново.')
            state.update(step='body',editing_field=True);self.store.set_state(uid,state)
            return [reply('Измените текст или объём задания. Оценку времени можно будет уточнить перед публикацией.',
                [[button('Назад','back'),button('Отмена','menu')]])]
        if text=='back':
            if state.get('edit_id'):return self.edit_menu(uid,state['edit_id'])
            previous={'body':'subject','due':'body','minutes':'due','confirm':'minutes'}.get(step)
            if not previous:return self.menu(uid)
            state['step']=previous;self.store.set_state(uid,state)
            if previous=='due':return self.date_picker(uid)
            if previous=='minutes':return self.minute_picker(uid)
            return [reply('Выберите или напишите предмет:' if previous=='subject' else 'Введите текст задания:',
                [[button('Отмена','menu')]])]
        if text.startswith('suggest:'):
            _,nonce,day=text.split(':')
            if step!='confirm' or nonce!=state.get('nonce'):raise ValueError('Предложение устарело. Откройте задание заново.')
            self.require(uid,('admin','teacher'))
            prep=parse_date(day,self.today())
            state['draft']['due']=prep.isoformat()
            if state['draft'].get('lesson_due'):state['draft']['lesson_due']=(prep+timedelta(days=1)).isoformat()
            self.store.set_state(uid,state)
            return self.preview(uid,state['draft'])
        if text.startswith('confirm:'):
            self.require(uid,('admin','teacher'))
            if step!='confirm' or text.split(':')[1]!=state.get('nonce'):
                raise ValueError('Эта кнопка уже использована или устарела. /add — новое задание.')
            member=self.store.member(uid);draft=state['draft']
            total=self.day_load(member['class_id'],draft['due'],state.get('edit_id'))+draft['minutes']
            if total>member['daily_limit'] and total!=state.get('preview_total'):
                result=self.preview(uid,draft);result[0]['text']='Нагрузка изменилась. Проверьте её ещё раз.\n\n'+result[0]['text'];return result
            return self.publish(uid,draft)
        # Кнопки старой версии due: сохраняют прежний смысл «до конца дня».
        if text.startswith('due:') and step=='due':
            state['deadline_mode']='evening';state['draft']['lesson_due']=None
            self.store.set_state(uid,state)
        elif step=='due' and (text.startswith('lesson:') or ':' not in text and not text.startswith('/') and text not in ('menu','load','plan','help','add','tasks','manage','calendar_noop')):
            try:
                chosen=parse_date(text.split(':',1)[1] if text.startswith('lesson:') else text,self.today())
                if state.get('deadline_mode')=='lesson' or text.startswith('lesson:'):
                    if chosen<=self.today():raise ValueError('Выберите день урока начиная с завтрашнего.')
                    state['draft']['lesson_due']=chosen.isoformat();state['deadline_mode']='lesson'
                    chosen-=timedelta(days=1)
                self.store.set_state(uid,state)
                text='due:'+chosen.isoformat()
                # Обработка подготовленной даты ниже выполняется базовым сценарием без повторного преобразования.
            except ValueError:
                return self.date_picker(uid,error='Выберите дату урока кнопкой. Подготовку запланируем накануне.')
        if state.get('editing_field') and step in ('subject','body','minutes') and text not in ('menu','/menu','/cancel','help','/help'):
            value=text.split(':',1)[1] if text.startswith(('subject:','minutes:')) else text
            if step=='minutes':value=bounded_int(value,1,600)
            elif not 1<=len(value)<=(60 if step=='subject' else 700):raise ValueError('Проверьте длину введённого текста.')
            changed = state['draft'].get(step) != value
            state['draft'][step]=value;self.store.set_state(uid,state)
            if self.estimator and changed and step in ('subject', 'body'):
                state['step'] = 'minutes'
                self.store.set_state(uid, state)
                return self.minute_picker(uid)
            return self.preview(uid,state['draft'])
        if text in ('menu','/menu','plan','/plan'):
            self.store.set_state(uid,{})
        messages=super()._handle(uid,text)
        if text=='add':
            current=self.store.state(uid);current['deadline_mode']='lesson';self.store.set_state(uid,current)
            if self.db.execute('SELECT 1 FROM teacher_defaults WHERE user_id=?',(uid,)).fetchone():
                messages[0]['attachments'][0]['payload']['buttons'].insert(0,[button('Быстрое ДЗ','add_quick')])
        current=self.store.state(uid)
        if self.estimator and step == 'body' and current.get('step') == 'due':
            draft = current['draft']
            if draft.get('body') != state.get('draft', {}).get('body') and 'minutes' in draft:
                current['previous_minutes'] = draft.pop('minutes')
                self.store.set_state(uid, current)
        if current.get('step') in ('body','due','minutes'):
            for m in messages:
                for a in m.get('attachments',[]):
                    if a['type']=='inline_keyboard' and not any(b.get('payload')=='back' for row in a['payload']['buttons'] for b in row):
                        a['payload']['buttons'].append([button('‹ Назад','back')])
        return messages

    def manual_minutes(self, uid, state):
        messages = super().minute_picker(uid)
        previous = state['draft'].get('minutes', state.get('previous_minutes'))
        if previous:
            rows = messages[0]['attachments'][0]['payload']['buttons']
            rows.insert(0, [button(f'Оставить {previous} мин', f'minutes:{previous}')])
        return messages

    def minute_picker(self, uid=None):
        if not self.estimator or uid is None:
            return super().minute_picker(uid)
        member = self.require(uid, ('admin', 'teacher'))
        state = self.store.state(uid)
        draft = state['draft']
        match = re.match(r'^\s*(?:класс\s+)?(1[01]|[1-9])(?!\d)', member['name'], re.IGNORECASE)
        grade = int(match[1]) if match else None
        estimate_input = json.dumps([draft['subject'], draft['body'], grade], ensure_ascii=False)
        fingerprint = hashlib.sha256(estimate_input.encode('utf-8')).hexdigest()
        cached = state.get('time_estimate') or {}
        if cached.get('fingerprint') != fingerprint:
            cached = {
                'fingerprint': fingerprint,
                'token': secrets.token_hex(5),
                'suggestion': self.estimator(draft['subject'], draft['body'], grade),
            }
            state['time_estimate'] = cached
            self.store.set_state(uid, state)
        suggestion = cached['suggestion']
        if not suggestion:
            messages = self.manual_minutes(uid, state)
            messages[0]['text'] = 'Не удалось оценить время.\n\n' + messages[0]['text']
            return messages
        minutes = suggestion['minutes']
        message = reply(
            f'Ориентир · {minutes} мин\nВид работы — {suggestion["kind"]}.\n\n'
            'Подтвердите оценку или напишите своё число минут.',
            [[button(f'Принять {minutes} мин', f'estimate:{cached["token"]}')],
             [button('Своё время', 'own_minutes')],
             [button('‹ Назад', 'back'), button('Отмена', 'menu')]],
        )
        message['_visual'] = self.visual(uid, '', message)
        message['_visual'].update(subtitle=f'Предлагаем {minutes} мин', minutes=minutes)
        return [message]

    def set_actual(self,uid,task_id,actual):
        self.require(uid,('student',));self.task(uid,task_id)
        done=self.db.execute('SELECT 1 FROM done WHERE user_id=? AND task_id=?',(uid,task_id)).fetchone()
        if not done:raise ValueError('Сначала отметьте задание выполненным.')
        info=self.db.execute('SELECT log_id FROM completion_info WHERE user_id=? AND task_id=?',(uid,task_id)).fetchone()
        if info and info['log_id']:
            previous=self.db.execute('SELECT COALESCE(SUM(worked),0) FROM study_log WHERE user_id=? AND task_id=? AND id<>?',
                (uid,task_id,info['log_id'])).fetchone()[0]
            if actual<previous:raise ValueError(f'Уже учтены части на {previous} мин. Введите время за всё задание не меньше этого значения.')
            self.db.execute('UPDATE study_log SET worked=? WHERE id=? AND user_id=?',(actual-previous,info['log_id'],uid))
        self.db.execute('UPDATE done SET actual=? WHERE user_id=? AND task_id=?',(actual,uid,task_id))

    def day_load(self,class_id,day,exclude=None):
        return self.db.execute('SELECT COALESCE(SUM(minutes),0) FROM tasks WHERE class_id=? AND due=? AND id<>?',
            (class_id,day,exclude or '')).fetchone()[0]

    def preview(self,uid,draft):
        member=self.require(uid,('admin','teacher'));state=self.store.state(uid)
        if state.get('edit_id'):self.owned_task(uid,state['edit_id'])
        total=self.day_load(member['class_id'],draft['due'],state.get('edit_id'))+draft['minutes']
        nonce=secrets.token_hex(5);state.update(step='confirm',draft=draft,nonce=nonce,preview_total=total)
        self.store.set_state(uid,state);high=total>member['daily_limit']
        title='⚠ Высокая нагрузка' if high else 'Проверка задания'
        text=f'{title} · {member["name"]}\n\n{draft["subject"]}: {draft["body"]}\n{self.deadline(draft)}\n'
        text+=f'Подготовить до конца {pretty_date(draft["due"])}. Оценка: {draft["minutes"]} мин.\n\n'
        text+=f'На вечер подготовки: {total} мин с этим заданием. Порог класса: {member["daily_limit"]} мин.'
        if state.get('agent_draft'):
            text = (f'{title} · {member["name"]}\n\n{draft["subject"]} · {draft["minutes"]} мин\n'
                    f'{draft["body"]}\n{self.deadline(draft)}\n'
                    f'На {pretty_date(draft["due"])} — {total} из {member["daily_limit"]} мин.')
        if state.get('edit_id'):
            finished=self.db.execute('SELECT COUNT(*) FROM done WHERE task_id=?',(state['edit_id'],)).fetchone()[0]
            if finished:text+=f'\n\nУже выполнено у {finished} учеников. Их отметки сохранятся; дополнительную работу задайте новым заданием.'
        if high:text+=f'\nПревышение: {total-member["daily_limit"]} мин.'
        confirm='Сохранить изменения' if state.get('edit_id') else ('Оставить как есть' if high else 'Опубликовать')
        rows=[[button(confirm,f'confirm:{nonce}')],
              [button('Изменить срок',f'move:{nonce}'),button('Оценка минут',f'editminutes:{nonce}')]]
        rows.append([button('Изменить текст / объём',f'draft_body:{nonce}')])
        if high:
            suggestions=0
            for offset in range(1,8):
                day=date.fromisoformat(draft['due'])+timedelta(days=offset)
                if day>self.today()+timedelta(days=180):break
                n=self.day_load(member['class_id'],day.isoformat(),state.get('edit_id'))+draft['minutes']
                if n<=member['daily_limit']:
                    lesson=day+timedelta(days=1) if draft.get('lesson_due') else day
                    rows.append([button(f'{pretty_date(lesson)} · {n} мин',f'suggest:{nonce}:{day}')]);suggestions+=1
                if suggestions==2:break
            text+='\nПредлагаемые даты согласуйте с расписанием уроков.'
        rows.append([button('‹ Назад','back'),button('Отмена','menu')])
        return [reply(text,rows)]

    def publish(self,uid,draft):
        member=self.require(uid,('admin','teacher'));state=self.store.state(uid)
        if draft['due']<self.today().isoformat():raise ValueError('Вечер подготовки уже прошёл. Измените дату урока.')
        if draft.get('lesson_due') and date.fromisoformat(draft['lesson_due'])-timedelta(days=1)!=date.fromisoformat(draft['due']):
            raise ValueError('Проверьте дату урока и подготовки.')
        task_id=state.get('edit_id') or secrets.token_hex(6)
        if state.get('edit_id'):
            old=self.owned_task(uid,task_id)
            if old['revision']!=state.get('base_revision',old['revision']):
                raise ValueError('Другой пользователь изменил задание. Откройте его заново, чтобы сохранить актуальную версию.')
            self.db.execute('UPDATE tasks SET subject=?,body=?,due=?,minutes=? WHERE id=?',
                (draft['subject'],draft['body'],draft['due'],draft['minutes'],task_id));revision=old['revision']+1
        else:
            self.db.execute('INSERT INTO tasks(id,class_id,creator,subject,body,due,minutes) VALUES (?,?,?,?,?,?,?)',
                (task_id,member['class_id'],uid,draft['subject'],draft['body'],draft['due'],draft['minutes']));revision=0
        self.db.execute('INSERT OR REPLACE INTO task_details VALUES (?,?,?,?)',
            (task_id,draft.get('lesson_due'),revision,self.today().isoformat()))
        self.db.execute('INSERT OR REPLACE INTO teacher_defaults VALUES (?,?,?,?)',(uid,draft['subject'],draft['body'],draft['minutes']))
        self.store.set_state(uid,{})
        if state.get('agent_draft'):
            title = 'Изменения сохранены' if state.get('edit_id') else 'Задание опубликовано'
            message = reply(f'{title} · {member["name"]}\n{draft["subject"]} · {draft["minutes"]} мин · {self.deadline(draft)}',
                            [[button('Mera Agent', 'mera_agent'), button('К заданиям', 'tasks')]])
            message['_visual'] = {'kind': 'tasks', 'title': 'ЗАДАНИЕ\nСОХРАНЕНО',
                                  'subtitle': title, 'class_name': member['name'], 'count': 1,
                                  'total': draft['minutes'], 'entries': [{**draft, 'due': self.deadline(draft)}]}
            return [message]
        return self.menu(uid,'Изменения сохранены.\n\n' if state.get('edit_id') else 'Задание опубликовано.\n\n')

    def date_picker(self,uid,month=None,error=''):
        state=self.store.state(uid)
        if state.get('deadline_mode')!='lesson':return super().date_picker(uid,month,error)
        self.require(uid,('admin','teacher'))
        if state.get('step')!='due':raise ValueError('Сначала начните добавление задания.')
        today=self.today();earliest=today+timedelta(days=1)
        state['calendar_month']=month.isoformat() if month else None;self.store.set_state(uid,state)
        rows=[]
        if month is None:
            for pair in ((1,2),(3,4),(5,6)):
                rows.append([button('Завтра' if i==1 else pretty_date(today+timedelta(days=i)),
                    'lesson:'+(today+timedelta(days=i)).isoformat()) for i in pair])
            rows.append([button('Календарь месяца',f'calendar:{earliest:%Y-%m}')])
        else:
            rows.append([button(d,'calendar_noop') for d in WEEKDAYS])
            for week in calendar.monthcalendar(month.year,month.month):
                row=[]
                for n in week:
                    day=month.replace(day=n) if n else None
                    valid=day and earliest<=day<=today+timedelta(days=180)
                    row.append(button(str(n) if valid else '·','lesson:'+day.isoformat() if valid else 'calendar_noop'))
                rows.append(row)
            prev=(month-timedelta(days=1)).replace(day=1);nxt=(month.replace(day=28)+timedelta(days=4)).replace(day=1)
            nav=[]
            if prev>=today.replace(day=1):nav.append(button('‹ '+MONTH_TITLES[prev.month-1],f'calendar:{prev:%Y-%m}'))
            if nxt<=today+timedelta(days=180):nav.append(button(MONTH_TITLES[nxt.month-1]+' ›',f'calendar:{nxt:%Y-%m}'))
            if nav:rows.append(nav)
        rows.append([button('‹ Назад','back'),button('Отмена','menu')])
        message=reply((error+'\n\n' if error else '')+'К какому уроку нужно задание?\nВыберите день урока — подготовку запланируем накануне.',rows)
        message['_visual']={'kind':'calendar','title':'К КАКОМУ\nУРОКУ?','subtitle':'Подготовка — накануне выбранного дня','month':(month or earliest).replace(day=1).isoformat(),
            'today':today.isoformat(),'earliest':earliest.isoformat(),'selected_due':state['draft'].get('lesson_due'),'subject':state['draft'].get('subject',''),'body':state['draft'].get('body','')}
        return [message]

    def edit_menu(self,uid,task_id):
        task=self.owned_task(uid,task_id);self.store.set_state(uid,{})
        result=reply(f'{task["subject"]}: {task["body"]}\n{self.deadline(task)} · {task["minutes"]} мин\n\nЧто изменить?',
            [[button(label,f'edit_field:{task_id}:{field}') for label,field in pair] for pair in
             ((('Предмет','subject'),('Текст задания','body')),(('Срок','due'),('Минуты','minutes')))]+
            [[button('К заданиям','tasks')]])
        result['_visual']={'kind':'tasks','title':'ИЗМЕНИТЬ\nЗАДАНИЕ','subtitle':'Изменения увидят ученики после сохранения','count':1,'total':task['minutes'],
            'entries':[{'subject':task['subject'],'body':task['body'],'minutes':task['minutes'],'due':self.deadline(task)}]}
        return [result]

    def task_list(self,uid,page=0):
        messages=super().task_list(uid,page)
        for row in messages[0]['attachments'][0]['payload']['buttons']:
            if len(row)==1 and row[0].get('payload','').startswith('delete:'):
                task_id=row[0]['payload'].split(':')[1]
                row.insert(0,button('Изменить',f'edit:{task_id}'));row[1]['text']='Удалить'
        return messages

    def completed(self,uid,page=0):
        member=self.require(uid,('student',))
        tasks=self.db.execute('''SELECT t.*,d.actual FROM done d JOIN tasks t ON t.id=d.task_id
            WHERE d.user_id=? AND t.class_id=? ORDER BY t.due DESC,t.id''',(uid,member['class_id'])).fetchall()
        pages=max(1,(len(tasks)+2)//3);page=min(max(0,page),pages-1);visible=tasks[page*3:page*3+3]
        lines=['Выполненные задания',''];rows=[];entries=[]
        for task in visible:
            lines.append(f'{task["subject"]}: {task["body"]}\nОценка {task["minutes"]} мин; факт: '+(f'{task["actual"]} мин' if task['actual'] is not None else 'не указан')+'\n')
            rows.append([button('Отменить: '+task['subject'][:24],f'undo:{task["id"]}')])
            entries.append({'subject':task['subject'],'body':task['body'],'minutes':task['actual'] if task['actual'] is not None else task['minutes'],'due':'Факт' if task['actual'] is not None else 'Оценка'})
        if not tasks:lines.append('Пока нет выполненных заданий. Они появятся здесь после отметки «Всё готово».')
        nav=[]
        if page:nav.append(button('‹ Назад',f'completed:{page-1}'))
        if page+1<pages:nav.append(button('Дальше ›',f'completed:{page+1}'))
        if nav:rows.append(nav)
        rows.append([button('К плану','plan')]);message=reply('\n'.join(lines),rows)
        message['_visual']={'kind':'tasks','title':'УЖЕ\nГОТОВО','subtitle':'Ошибочную отметку можно отменить','entries':entries,'count':len(tasks),'total':sum(t['minutes'] for t in tasks),'page':page+1,'pages':pages}
        return [message]

    def visual(self,uid,action,message):
        model=super().visual(uid,action,message);state=self.store.state(uid)
        if state.get('draft',{}).get('lesson_due') and model['kind'] in ('wizard','warning','preview'):
            model['due']=self.deadline(state['draft'])
            model['prepare_day']=pretty_date(state['draft']['due']) if state['draft'].get('due') else ''
        if model['kind']=='load':model['subtitle']='Нагрузка по вечерам подготовки'
        return model
