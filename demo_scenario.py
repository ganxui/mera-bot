"""Воспроизводимый проход трёх ролей; только тестовые данные, без сети."""
import sys
from mera import Engine, Store


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    store = Store(':memory:')
    engine = Engine(store)

    def act(uid, command, show=True, is_callback=False):
        messages = engine.handle(uid, command, is_callback=is_callback)
        if show:
            print('\n' + '\n\n'.join(m['text'] for m in messages))
        return messages

    def publish(subject, minutes):
        for command in ('add', subject, 'Тестовое задание для демонстрации', 'завтра'):
            act(2, command, False)
        messages = act(2, str(minutes))
        action = messages[0]['attachments'][0]['payload']['buttons'][0][0]['payload']
        act(2, action, False, is_callback=True)

    print('МЕРА · ДЕМОНСТРАЦИЯ. Все данные вымышленные, сеть не используется.')
    print('\n1. Классный руководитель выбирает 9А и получает два кода.')
    act(1, 'enroll:head', False, is_callback=True)
    act(1, 'class_grade:9', False, is_callback=True)
    act(1, 'class_pick:9:А', False, is_callback=True)
    member = store.member(1)
    print('Создан класс с отдельными кодами учителя и ученика.')
    act(2, 'enroll:teacher', False, is_callback=True)
    act(2, 'Робототехника', False)
    act(2, member['teacher_code'])
    act(1, 'class_subjects:0', is_callback=True)
    act(3, 'enroll:student', False, is_callback=True)
    act(3, member['student_code'], False)
    print('\n2. Учитель публикует задания. При превышении 180 минут видит предупреждение.')
    for subject, minutes in (('Робототехника', 60), ('Русский язык', 60), ('Физика', 45), ('История', 45)):
        publish(subject, minutes)
    act(2, 'load')
    print('\n3. Ученик задаёт доступное время, видит план и нехватку времени до урока.')
    act(3, 'free', False)
    act(3, '120 120 120 120 120 60 60')
    print('\n4. Ученик выполняет задание. У остальных учеников оно остаётся.')
    task = store.tasks(member['class_id'])[0]
    act(3, 'done:' + task['id'])
    act(3, f'actual:{task["id"]}:45', False)
    print('\nПлан после отметки:')
    act(3, 'plan')
    store.close()


if __name__ == '__main__':
    main()
