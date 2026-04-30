from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import mysql.connector
import os
import random
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'super_secret'

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'root',  # <--- ТВОЙ ПАРОЛЬ
    'database': 'digital_ocean',
    'charset': 'utf8mb4'
}


def get_db():
    return mysql.connector.connect(**db_config)


# --- ЛОГИН (ОБНОВЛЕННЫЙ С ПАРОЛЕМ ДЛЯ УЧЕНИКА) ---
@app.route('/', methods=['GET', 'POST'])
def login():
    msg = ''
    if request.method == 'POST':
        role = request.form.get('role')
        username = request.form.get('username')
        password = request.form.get('password')  # Теперь пароль обязателен для всех

        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        user = None

        if role == 'teacher':
            cursor.execute(
                'SELECT * FROM users WHERE username = %s AND password = %s AND role="teacher"',
                (username, password))
            user = cursor.fetchone()
            # Сценарий 2: Входит Ученик
        else:
            password = request.form.get('password')
            # Ищем ученика по имени
            cursor.execute('SELECT * FROM users WHERE username = %s AND role="student"', (username,))
            existing_student = cursor.fetchone()

            # ТЕПЕРЬ МЫ НИКОГО НЕ СОЗДАЕМ! Просто проверяем пароль.
            if existing_student:
                if existing_student['password'] == password:
                    user = existing_student
                else:
                    msg = 'Неверный пароль!'
            else:
                msg = 'Такого ученика нет! Попроси учителя создать тебе аккаунт.'

        cursor.close()
        conn.close()

        if user:
            session['loggedin'] = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name']
            return redirect('/teacher' if user['role'] == 'teacher' else '/student')
        elif not msg:
            msg = 'Неверный логин или пароль!'

    return render_template('login.html', msg=msg)


def refresh_user_shop(user_id, cursor):
    # 1. Получаем состояние
    cursor.execute('SELECT * FROM user_shop_state WHERE user_id = %s', (user_id,))
    state = cursor.fetchone()
    if not state:
        cursor.execute('INSERT INTO user_shop_state (user_id) VALUES (%s)', (user_id,))
        cursor.execute('SELECT * FROM user_shop_state WHERE user_id = %s', (user_id,))
        state = cursor.fetchone()

    # 2. Удаляем все НЕзакрепленные предметы с витрины
    cursor.execute('DELETE FROM user_shop_slots WHERE user_id = %s AND is_pinned = FALSE',
                   (user_id,))

    # Смотрим, сколько слотов свободно
    cursor.execute('SELECT artifact_id FROM user_shop_slots WHERE user_id = %s', (user_id,))
    pinned_items = [row['artifact_id'] for row in cursor.fetchall()]
    slots_to_fill = 4 - len(pinned_items)

    # Получаем инвентарь (чтобы не предлагать купленное)
    cursor.execute('SELECT artifact_id FROM inventory WHERE user_id = %s', (user_id,))
    owned_items = [row['artifact_id'] for row in cursor.fetchall()]
    exclude_ids = pinned_items + owned_items

    # 3. ЛОГИКА ГАРАНТА
    desired_id = state['desired_artifact_id']
    desired_spawned_now = False

    if desired_id and desired_id not in owned_items and slots_to_fill > 0:
        if state['pity_counter'] >= 2:
            # Выдаем гарант со скидкой!
            cursor.execute(
                'INSERT INTO user_shop_slots (user_id, artifact_id, has_discount) VALUES (%s, %s, TRUE)',
                (user_id, desired_id))
            cursor.execute(
                'UPDATE user_shop_state SET pity_counter = 0, desired_artifact_id = NULL WHERE user_id = %s',
                (user_id,))
            slots_to_fill -= 1
            exclude_ids.append(desired_id)
            desired_spawned_now = True

    # 4. ЗАПОЛНЯЕМ СЛОТЫ РАНДОМОМ
    cursor.execute('SELECT id, rarity FROM artifacts')
    all_arts = cursor.fetchall()

    for _ in range(slots_to_fill):
        # Шансы: 50% Обычный, 30% Особый, 20% Эпический
        roll = random.randint(1, 100)
        target_rarity = 'обычный' if roll <= 50 else ('особый' if roll <= 80 else 'эпический')

        # Ищем подходящие артефакты этой редкости, которых еще нет
        available = [a['id'] for a in all_arts if
                     a['rarity'] == target_rarity and a['id'] not in exclude_ids]

        # Если нужной редкости нет, берем любой доступный
        if not available:
            available = [a['id'] for a in all_arts if a['id'] not in exclude_ids]

        if available:
            chosen_id = random.choice(available)
            cursor.execute('INSERT INTO user_shop_slots (user_id, artifact_id) VALUES (%s, %s)',
                           (user_id, chosen_id))
            exclude_ids.append(chosen_id)
            if chosen_id == desired_id:
                desired_spawned_now = True

    # 5. ОБНОВЛЯЕМ СЧЕТЧИК ГАРАНТА И ТАЙМЕР
    # Вычисляем следующее 16:00 через 2 дня
    now = datetime.now()
    next_refresh = (now + timedelta(days=2)).replace(hour=16, minute=0, second=0, microsecond=0)

    new_pity = 0 if desired_spawned_now else (state['pity_counter'] + 1 if desired_id else 0)
    cursor.execute(
        'UPDATE user_shop_state SET next_refresh = %s, pity_counter = %s WHERE user_id = %s',
        (next_refresh, new_pity, user_id))


# --- КАБИНЕТ УЧЕНИКА ---

@app.route('/student')
def student_dashboard():
    if 'user_id' not in session or session['role'] != 'student':
        return redirect('/')

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    user_id = session['user_id']

    # 1. ДАННЫЕ УЧЕНИКА
    cursor.execute('SELECT * FROM student_progress WHERE user_id = %s', (user_id,))
    student_data = cursor.fetchone()

    # 2. АКТИВНЫЙ МОНСТР
    cursor.execute('SELECT * FROM monsters WHERE is_active = TRUE LIMIT 1')
    monster = cursor.fetchone()
    if not monster:
        monster = {'name': 'Нет монстра', 'current_hp': 0, 'max_hp': 100, 'quarter': 0}

    # 3. ИНВЕНТАРЬ (Мои артефакты)
    cursor.execute('SELECT artifact_id FROM inventory WHERE user_id = %s', (user_id,))
    inventory_ids = [row['artifact_id'] for row in cursor.fetchall()]

    cursor.execute('''
        SELECT a.* FROM artifacts a
        JOIN inventory i ON a.id = i.artifact_id
        WHERE i.user_id = %s
    ''', (user_id,))
    my_inventory = cursor.fetchall()

    # 4. ЛОГИКА СЕТОВ АРТЕФАКТОВ
    cursor.execute('SELECT set_name, COUNT(*) as total_items FROM artifacts GROUP BY set_name')
    set_requirements = {row['set_name']: row['total_items'] for row in cursor.fetchall()}

    cursor.execute('''
        SELECT a.set_name, COUNT(i.id) as owned_items 
        FROM artifacts a 
        JOIN inventory i ON a.id = i.artifact_id 
        WHERE i.user_id = %s 
        GROUP BY a.set_name
    ''', (user_id,))
    owned_sets_info = {row['set_name']: row['owned_items'] for row in cursor.fetchall()}

    cursor.execute('SELECT set_name, status FROM set_requests WHERE student_id = %s', (user_id,))
    requests_info = {row['set_name']: row['status'] for row in cursor.fetchall()}

    sets_data = []
    for set_name, total in set_requirements.items():
        owned = owned_sets_info.get(set_name, 0)
        status = requests_info.get(set_name, None)
        sets_data.append({
            'name': set_name,
            'is_complete': (owned == total),
            'status': status,
            'owned': owned,
            'total': total
        })

    # 5. ДОП. ЗАДАНИЯ
    cursor.execute('''
        SELECT * FROM extra_tasks 
        WHERE id NOT IN (SELECT task_id FROM completed_tasks WHERE student_id = %s)
    ''', (user_id,))
    active_tasks = cursor.fetchall()

    # 6. МАГАЗИН (НОВЫЙ ГАЧА-МЕХАНИЗМ)
    cursor.execute('SELECT * FROM artifacts')
    all_artifacts = cursor.fetchall()  # Нужно для выпадающего списка гарантов

    cursor.execute('SELECT * FROM user_shop_state WHERE user_id = %s', (user_id,))
    shop_state = cursor.fetchone()

    # Обновляем, если пришло время или магазина еще нет
    if not shop_state or shop_state['next_refresh'] <= datetime.now():
        refresh_user_shop(user_id, cursor)
        conn.commit()
        cursor.execute('SELECT * FROM user_shop_state WHERE user_id = %s', (user_id,))
        shop_state = cursor.fetchone()

    # Загружаем 4 товара с витрины
    cursor.execute('''
        SELECT a.*, s.is_pinned, s.has_discount 
        FROM user_shop_slots s
        JOIN artifacts a ON s.artifact_id = a.id
        WHERE s.user_id = %s
    ''', (user_id,))
    shop_items = cursor.fetchall()

    # Считаем скидку 10%, если она выпала по гаранту
    for item in shop_items:
        if item['has_discount']:
            item['old_price'] = item['price']
            item['price'] = int(item['price'] * 0.9)

    conn.close()

    # Отправляем ВСЕ переменные в HTML (их стало много!)
    return render_template('student.html',
                           student=student_data,
                           monster=monster,
                           artifacts=all_artifacts,
                           inventory_ids=inventory_ids,
                           my_inventory=my_inventory,
                           sets_data=sets_data,
                           active_tasks=active_tasks,
                           shop_state=shop_state,
                           shop_items=shop_items)  # Передаем сеты в шаблон


# --- КАБИНЕТ УЧИТЕЛЯ ---
@app.route('/teacher')
def teacher_dashboard():
    if 'user_id' not in session or session['role'] != 'teacher': return redirect('/')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    # 1. Ученики
    # 1. Ученики (ПОЛНАЯ СТАТИСТИКА ДЛЯ КАРТОЧЕК)
    cursor.execute('''
            SELECT 
                u.id, 
                u.full_name, 
                u.avatar_type, 
                sp.current_points, 
                sp.level, 
                sp.total_spent,
                (SELECT COUNT(*) FROM inventory WHERE user_id = u.id) as artifacts_count
            FROM users u 
            JOIN student_progress sp ON u.id = sp.user_id 
            WHERE u.role = "student"
        ''')
    students = cursor.fetchall()

    # 2. Заявки на сеты
    cursor.execute('''
        SELECT r.id, r.set_name, u.full_name 
        FROM set_requests r 
        JOIN users u ON r.student_id = u.id 
        WHERE r.status = 'pending'
    ''')
    pending_requests = cursor.fetchall()

    # 3. Настройки событий
    cursor.execute('SELECT * FROM grading_events')
    grading_events = cursor.fetchall()

    # 4. ПОСЛЕДНИЕ ДЕЙСТВИЯ (НОВОЕ!)
    cursor.execute('''
        SELECT u.full_name as student_name, t.reason as action, t.created_at as date 
        FROM transactions t 
        JOIN users u ON t.student_id = u.id 
        ORDER BY t.created_at DESC LIMIT 15
    ''')
    recent_actions = cursor.fetchall()

    # Форматируем дату, чтобы было красиво (например, 25.09.26)
    for act in recent_actions:
        if act['date']:
            act['date'] = act['date'].strftime('%d.%m.%y')
        # 5. СПИСОК ВСЕХ МОНСТРОВ (ДЛЯ УПРАВЛЕНИЯ)

    cursor.execute('SELECT * FROM monsters ORDER BY quarter ASC')
    all_monsters = cursor.fetchall()
    # Грузим все задания для админки
    cursor.execute('SELECT * FROM extra_tasks ORDER BY created_at DESC')
    all_tasks = cursor.fetchall()
    # 6. АРТЕФАКТЫ (ДЛЯ РЕДАКТОРА)
    cursor.execute('SELECT * FROM artifacts ORDER BY set_name, min_level')
    all_artifacts = cursor.fetchall()

    conn.close()
    return render_template('teacher.html',
                           students=students,
                           pending_requests=pending_requests,
                           grading_events=grading_events,
                           recent_actions=recent_actions,
                           monsters=all_monsters,
                           tasks=all_tasks,
                           all_artifacts=all_artifacts)


# --- API ---
@app.route('/api/buy_artifact', methods=['POST'])
def buy_artifact():
    if 'user_id' not in session: return jsonify(
        {'status': 'error', 'message': 'Не авторизован'}), 403
    data = request.json
    artifact_id = data.get('artifact_id')
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute('SELECT price, name FROM artifacts WHERE id = %s', (artifact_id,))
        artifact = cursor.fetchone()
        cursor.execute('SELECT current_points FROM student_progress WHERE user_id = %s', (user_id,))
        student = cursor.fetchone()
        if student['current_points'] < artifact['price']: return jsonify(
            {'status': 'error', 'message': 'Недостаточно баллов'})
        cursor.execute(
            'UPDATE student_progress SET current_points = current_points - %s, total_spent = total_spent + %s WHERE user_id = %s',
            (artifact['price'], artifact['price'], user_id))
        cursor.execute('INSERT INTO inventory (user_id, artifact_id) VALUES (%s, %s)',
                       (user_id, artifact_id))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: НАЧИСЛЕНИЕ БАЛЛОВ УЧИТЕЛЯМИ (ОБНОВЛЕНО ДЛЯ МАССОВОГО НАЧИСЛЕНИЯ) ---
@app.route('/api/give_points', methods=['POST'])
def give_points():
    if 'user_id' not in session or session['role'] != 'teacher':
        return jsonify({'status': 'error', 'message': 'Нет доступа'}), 403

    data = request.json
    # Теперь мы ждем СПИСОК id учеников (даже если ученик один, это будет список из одного элемента)
    student_ids = data.get('student_ids')
    amount = int(data.get('amount'))
    reason = data.get('reason')

    if not student_ids or len(student_ids) == 0:
        return jsonify({'status': 'error', 'message': 'Ученики не выбраны'})

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Проходимся циклом по всем выбранным ученикам
        for student_id in student_ids:
            # 1. Обновляем баланс
            cursor.execute('''
                UPDATE student_progress 
                SET current_points = current_points + %s, total_earned = total_earned + %s 
                WHERE user_id = %s
            ''', (amount, amount, student_id))

            # 2. Пишем в историю
            cursor.execute('''
                INSERT INTO transactions (student_id, teacher_id, amount, reason) 
                VALUES (%s, %s, %s, %s)
            ''', (student_id, session['user_id'], amount, reason))

        conn.commit()  # Сохраняем все изменения разом
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()  # Если ошибка - отменяем всё
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- НОВЫЕ API ДЛЯ СЕТОВ ---
@app.route('/api/request_set', methods=['POST'])
def request_set():
    if 'user_id' not in session or session['role'] != 'student': return jsonify(
        {'status': 'error'}), 403
    set_name = request.json.get('set_name')
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO set_requests (student_id, set_name) VALUES (%s, %s)',
                       (session['user_id'], set_name))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/approve_set', methods=['POST'])
def approve_set():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403
    request_id = request.json.get('request_id')
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE set_requests SET status = 'approved' WHERE id = %s", (request_id,))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: АТАКА МОНСТРА (СПИСАНИЕ У ВСЕГО КЛАССА) ---
@app.route('/api/monster_attack', methods=['POST'])
def monster_attack():
    # Проверка прав (только учитель)
    if 'user_id' not in session or session['role'] != 'teacher':
        return jsonify({'status': 'error', 'message': 'Нет доступа'}), 403

    amount = int(request.json.get('amount', 0))
    if amount <= 0:
        return jsonify({'status': 'error', 'message': 'Некорректный урон'})

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 1. Находим всех учеников
        cursor.execute('SELECT id FROM users WHERE role = "student"')
        students = cursor.fetchall()

        # 2. Проходимся по каждому и списываем баллы
        for st in students:
            student_id = st['id']

            # Списываем баллы (GREATEST не дает уйти в минус)
            cursor.execute('''
                UPDATE student_progress 
                SET current_points = GREATEST(0, current_points - %s) 
                WHERE user_id = %s
            ''', (amount, student_id))

            # Записываем событие в историю
            cursor.execute('''
                INSERT INTO transactions (student_id, teacher_id, amount, reason) 
                VALUES (%s, %s, %s, %s)
            ''', (student_id, session['user_id'], -amount, "Атака монстра"))

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: ПЕРЕКЛЮЧЕНИЕ МОНСТРА НА СЛЕДУЮЩЕГО ---
@app.route('/api/complete_monster', methods=['POST'])
def complete_monster():
    if 'user_id' not in session or session['role'] != 'teacher':
        return jsonify({'status': 'error', 'message': 'Нет доступа'}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # Находим текущего монстра
        cursor.execute('SELECT id, quarter FROM monsters WHERE is_active = TRUE LIMIT 1')
        active_monster = cursor.fetchone()

        if active_monster:
            # Отключаем текущего
            cursor.execute('UPDATE monsters SET is_active = FALSE WHERE id = %s',
                           (active_monster['id'],))
            # Ищем следующего (по четверти)
            cursor.execute('SELECT id FROM monsters WHERE quarter > %s ORDER BY quarter ASC LIMIT 1',
                           (active_monster['quarter'],))
            next_monster = cursor.fetchone()

            if next_monster:
                # Включаем следующего
                cursor.execute('UPDATE monsters SET is_active = TRUE WHERE id = %s',
                               (next_monster['id'],))
            else:
                return jsonify({'status': 'error', 'message': 'Это был последний монстр в году!'})
        else:
            # Если активных нет, включаем самого первого
            cursor.execute('UPDATE monsters SET is_active = TRUE ORDER BY quarter ASC LIMIT 1')

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: ПЕРЕВОД КЛАССА НА НОВЫЙ УРОВЕНЬ ---
@app.route('/api/levelup_class', methods=['POST'])
def levelup_class():
    if 'user_id' not in session or session['role'] != 'teacher':
        return jsonify({'status': 'error', 'message': 'Нет доступа'}), 403

    conn = get_db()
    cursor = conn.cursor()
    try:
        # Повышаем уровень всем ученикам (но не больше 7 уровня, как по ТЗ)
        cursor.execute('UPDATE student_progress SET level = level + 1 WHERE level < 7')
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: НАСТРОЙКИ СОБЫТИЙ (ДОБАВИТЬ/УДАЛИТЬ) ---
@app.route('/api/add_event', methods=['POST'])
def add_event():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403

    data = request.json
    name = data.get('name')
    # Переводим в числа, если пусто - ставим 0
    v5, v4, v3, v2 = int(data.get('v5', 0)), int(data.get('v4', 0)), int(data.get('v3', 0)), int(
        data.get('v2', 0))

    if not name: return jsonify({'status': 'error', 'message': 'Название не может быть пустым'})

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO grading_events (name, val_5, val_4, val_3, val_2) VALUES (%s, %s, %s, %s, %s)',
            (name, v5, v4, v3, v2))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/delete_event', methods=['POST'])
def delete_event():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403

    event_id = request.json.get('event_id')
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM grading_events WHERE id = %s', (event_id,))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: УЧЕНИК АТАКУЕТ МОНСТРА ---
@app.route('/api/attack_monster', methods=['POST'])
def attack_monster():
    # Проверка, что это ученик
    if 'user_id' not in session or session['role'] != 'student':
        return jsonify({'status': 'error', 'message': 'Нет доступа'}), 403

    amount = int(request.json.get('amount', 0))
    if amount <= 0:
        return jsonify({'status': 'error', 'message': 'Некорректный урон'})

    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    try:
        # 1. Проверяем баланс ученика
        cursor.execute('SELECT current_points FROM student_progress WHERE user_id = %s', (user_id,))
        student = cursor.fetchone()

        if student['current_points'] < amount:
            return jsonify({'status': 'error', 'message': 'Недостаточно баллов!'})

        # 2. Ищем активного монстра
        cursor.execute('SELECT id, current_hp FROM monsters WHERE is_active = TRUE LIMIT 1')
        monster = cursor.fetchone()

        if not monster:
            return jsonify({'status': 'error', 'message': 'Монстр не найден!'})
        if monster['current_hp'] <= 0:
            return jsonify({'status': 'error', 'message': 'Монстр уже побежден!'})

        # 3. Списываем баллы у ученика
        cursor.execute('''
            UPDATE student_progress 
            SET current_points = current_points - %s, total_spent = total_spent + %s 
            WHERE user_id = %s
        ''', (amount, amount, user_id))

        # 4. Наносим урон монстру (ХП не упадет ниже нуля благодаря GREATEST)
        cursor.execute('''
            UPDATE monsters 
            SET current_hp = GREATEST(0, current_hp - %s) 
            WHERE id = %s
        ''', (amount, monster['id']))

        # 5. Пишем в историю транзакций
        cursor.execute('''
            INSERT INTO transactions (student_id, amount, reason) 
            VALUES (%s, %s, %s)
        ''', (user_id, -amount, "Урон монстру"))

        conn.commit()
        return jsonify({'status': 'success'})

    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: ДОБАВИТЬ НОВОГО МОНСТРА (С КАРТИНКОЙ) ---
@app.route('/api/add_monster', methods=['POST'])
def add_monster():
    if 'user_id' not in session or session['role'] != 'teacher':
        return jsonify({'status': 'error', 'message': 'Нет доступа'}), 403

    # Теперь мы принимаем данные через request.form, а не request.json, т.к. передаем файл
    name = request.form.get('name')
    max_hp = int(request.form.get('max_hp', 0))
    image_file = request.files.get('image')  # Достаем файл картинки

    if not name or max_hp <= 0:
        return jsonify({'status': 'error', 'message': 'Некорректные данные'})

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute('SELECT MAX(quarter) as max_q FROM monsters')
        res = cursor.fetchone()
        next_quarter = (res['max_q'] or 0) + 1

        cursor.execute('SELECT id FROM monsters WHERE is_active = TRUE')
        is_active = False if cursor.fetchone() else True

        # Сначала сохраняем монстра со стандартной картинкой
        cursor.execute('''
            INSERT INTO monsters (name, quarter, max_hp, current_hp, is_active, image)
            VALUES (%s, %s, %s, %s, %s, 'boss.png')
        ''', (name, next_quarter, max_hp, max_hp, is_active))

        monster_id = cursor.lastrowid  # Получаем ID только что созданного босса

        # Если учитель загрузил файл:
        if image_file and image_file.filename != '':
            # Узнаем расширение файла (например, png или jpg)
            ext = image_file.filename.rsplit('.', 1)[
                1].lower() if '.' in image_file.filename else 'png'
            # Придумываем уникальное имя: например, boss_5.png
            new_filename = f"boss_{monster_id}.{ext}"

            # Сохраняем файл физически в папку static/img
            filepath = os.path.join('static', 'img', new_filename)
            image_file.save(filepath)

            # Обновляем запись в БД, вписывая туда новое имя файла
            cursor.execute('UPDATE monsters SET image = %s WHERE id = %s',
                           (new_filename, monster_id))

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: АКТИВИРОВАТЬ БОССА ВРУЧНУЮ ---
@app.route('/api/activate_monster', methods=['POST'])
def activate_monster():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403
    monster_id = request.json.get('monster_id')
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Сначала выключаем всех боссов
        cursor.execute('UPDATE monsters SET is_active = FALSE')
        # Включаем только выбранного
        cursor.execute('UPDATE monsters SET is_active = TRUE WHERE id = %s', (monster_id,))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        cursor.close()
        conn.close()


# --- API: УДАЛИТЬ БОССА ---
@app.route('/api/delete_monster', methods=['POST'])
def delete_monster():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403
    monster_id = request.json.get('monster_id')
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM monsters WHERE id = %s', (monster_id,))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        cursor.close()
        conn.close()


# --- API: РЕДАКТИРОВАТЬ БОССА ---
@app.route('/api/edit_monster', methods=['POST'])
def edit_monster():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403

    # Так как тут может быть картинка, используем form, а не json
    monster_id = request.form.get('id')
    name = request.form.get('name')
    quarter = int(request.form.get('quarter', 1))  # Теперь это просто "Порядковый номер / Этап"
    max_hp = int(request.form.get('max_hp', 1))
    current_hp = int(request.form.get('current_hp', max_hp))
    image_file = request.files.get('image')

    conn = get_db()
    cursor = conn.cursor()
    try:
        # Обновляем текстовые данные и ХП
        cursor.execute('''
            UPDATE monsters 
            SET name=%s, quarter=%s, max_hp=%s, current_hp=%s 
            WHERE id=%s
        ''', (name, quarter, max_hp, current_hp, monster_id))

        # Если загрузили новую картинку - обновляем и её
        if image_file and image_file.filename != '':
            ext = image_file.filename.rsplit('.', 1)[
                1].lower() if '.' in image_file.filename else 'png'
            new_filename = f"boss_{monster_id}.{ext}"
            filepath = os.path.join('static', 'img', new_filename)
            image_file.save(filepath)
            cursor.execute('UPDATE monsters SET image = %s WHERE id = %s',
                           (new_filename, monster_id))

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        cursor.close()
        conn.close()


# --- API: УЧИТЕЛЬ ДОБАВЛЯЕТ ДОП. ЗАДАНИЕ ---
@app.route('/api/add_task', methods=['POST'])
def add_task():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403

    title = request.form.get('title')
    content = request.form.get('content')
    answer = request.form.get('answer', '').strip().lower()  # Ответ храним в нижнем регистре
    points = int(request.form.get('points', 0))
    image_file = request.files.get('image')

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO extra_tasks (title, content, correct_answer, reward_points) VALUES (%s, %s, %s, %s)',
            (title, content, answer, points))
        task_id = cursor.lastrowid

        # Если прикрепили картинку
        if image_file and image_file.filename != '':
            ext = image_file.filename.rsplit('.', 1)[1].lower()
            new_filename = f"task_{task_id}.{ext}"
            filepath = os.path.join('static', 'img', 'tasks', new_filename)
            image_file.save(filepath)
            cursor.execute('UPDATE extra_tasks SET image = %s WHERE id = %s',
                           (new_filename, task_id))

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# --- API: УЧЕНИК РЕШАЕТ ЗАДАНИЕ ---
@app.route('/api/submit_task', methods=['POST'])
def submit_task():
    if 'user_id' not in session or session['role'] != 'student': return jsonify(
        {'status': 'error'}), 403

    task_id = request.json.get('task_id')
    user_answer = request.json.get('answer',
                                   '').strip().lower()  # Сравниваем без учета регистра и пробелов
    user_id = session['user_id']

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute('SELECT * FROM extra_tasks WHERE id = %s', (task_id,))
        task = cursor.fetchone()
        if not task: return jsonify({'status': 'error', 'message': 'Задание не найдено'})

        if user_answer == task['correct_answer']:
            # 1. Начисляем баллы
            cursor.execute(
                'UPDATE student_progress SET current_points = current_points + %s, total_earned = total_earned + %s WHERE user_id = %s',
                (task['reward_points'], task['reward_points'], user_id))
            # 2. Отмечаем как решенное
            cursor.execute('INSERT INTO completed_tasks (student_id, task_id) VALUES (%s, %s)',
                           (user_id, task_id))
            # 3. Пишем в историю для учителя
            cursor.execute(
                'INSERT INTO transactions (student_id, amount, reason) VALUES (%s, %s, %s)',
                (user_id, task['reward_points'], f"Решено задание: {task['title']}"))

            conn.commit()
            return jsonify({'status': 'success',
                            'message': f"Верно! Начислено {task['reward_points']} баллов!"})
        else:
            return jsonify({'status': 'error', 'message': 'Неверный ответ. Попробуй еще раз!'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# ==========================================
# --- API: МАГАЗИН (ГАЧА-МЕХАНИКИ) ---
# ==========================================

@app.route('/api/toggle_pin', methods=['POST'])
def toggle_pin():
    if 'user_id' not in session or session['role'] != 'student':
        return jsonify({'status': 'error', 'message': 'Нет доступа'}), 403

    artifact_id = request.json.get('artifact_id')
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # Проверяем, не пытается ли он закрепить артефакт, который уже стоит в гаранте
        cursor.execute('SELECT desired_artifact_id FROM user_shop_state WHERE user_id = %s',
                       (user_id,))
        state = cursor.fetchone()

        if state and str(state['desired_artifact_id']) == str(artifact_id):
            return jsonify({'status': 'error',
                            'message': 'Нельзя закрепить артефакт, который выбран как гарант!'})

        # Меняем статус закрепления на противоположный (был закреплен - открепится, и наоборот)
        cursor.execute(
            'UPDATE user_shop_slots SET is_pinned = NOT is_pinned WHERE user_id = %s AND artifact_id = %s',
            (user_id, artifact_id))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/set_desired', methods=['POST'])
def set_desired():
    if 'user_id' not in session or session['role'] != 'student':
        return jsonify({'status': 'error', 'message': 'Нет доступа'}), 403

    artifact_id = request.json.get('artifact_id')
    # Если прислали пустую строку (выбрали "Не выбран"), превращаем в NULL для базы
    if not artifact_id:
        artifact_id = None

    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Ставим гарант и сбрасываем счетчик неудач
        cursor.execute(
            'UPDATE user_shop_state SET desired_artifact_id = %s, pity_counter = 0 WHERE user_id = %s',
            (artifact_id, user_id))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/force_refresh', methods=['POST'])
def force_refresh():
    if 'user_id' not in session or session['role'] != 'student':
        return jsonify({'status': 'error'}), 403

    # ЧИТ-КНОПКА: Перематываем таймер магазина в 2000 год, чтобы при обновлении страницы он сработал
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE user_shop_state SET next_refresh = "2000-01-01" WHERE user_id = %s',
                   (session['user_id'],))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


# --- API: УПРАВЛЕНИЕ УЧЕНИКАМИ (РЕДАКТОР) ---
@app.route('/api/add_student', methods=['POST'])
def add_student():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403

    name = request.json.get('name', '').strip()
    password = request.json.get('password', '').strip()
    gender = request.json.get('gender', 'boy')

    if not name or not password:
        return jsonify({'status': 'error', 'message': 'Имя и пароль обязательны!'})

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        # Проверяем, нет ли уже такого имени
        cursor.execute('SELECT id FROM users WHERE username = %s AND role="student"', (name,))
        if cursor.fetchone():
            return jsonify({'status': 'error', 'message': 'Ученик с таким именем уже существует!'})

        # 1. Создаем пользователя
        cursor.execute(
            'INSERT INTO users (username, password, role, avatar_type, full_name) VALUES (%s, %s, "student", %s, %s)',
            (name, password, gender, name))
        user_id = cursor.lastrowid

        # 2. Выдаем ему кошелек и магазин
        cursor.execute('INSERT INTO student_progress (user_id) VALUES (%s)', (user_id,))
        cursor.execute('INSERT INTO user_shop_state (user_id) VALUES (%s)', (user_id,))

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/delete_student', methods=['POST'])
def delete_student():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403

    student_id = request.json.get('student_id')
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Вручную удаляем все хвосты ученика, чтобы база данных не ругалась на связи
        cursor.execute('DELETE FROM inventory WHERE user_id = %s', (student_id,))
        cursor.execute('DELETE FROM transactions WHERE student_id = %s', (student_id,))
        cursor.execute('DELETE FROM completed_tasks WHERE student_id = %s', (student_id,))
        cursor.execute('DELETE FROM set_requests WHERE student_id = %s', (student_id,))
        cursor.execute('DELETE FROM student_progress WHERE user_id = %s', (student_id,))
        cursor.execute('DELETE FROM user_shop_state WHERE user_id = %s', (student_id,))
        cursor.execute('DELETE FROM user_shop_slots WHERE user_id = %s', (student_id,))

        # Удаляем самого ученика
        cursor.execute('DELETE FROM users WHERE id = %s AND role="student"', (student_id,))

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


# ==========================================
# --- API: РЕДАКТОР АРТЕФАКТОВ ---
# ==========================================

@app.route('/api/add_artifact', methods=['POST'])
def add_artifact():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403

    name = request.form.get('name')
    set_name = request.form.get('set_name', 'Без сета')
    rarity = request.form.get('rarity', 'обычный')
    price = int(request.form.get('price', 50))
    min_level = int(request.form.get('min_level', 1))
    image_file = request.files.get('image')

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO artifacts (name, price, min_level, set_name, rarity) 
            VALUES (%s, %s, %s, %s, %s)
        ''', (name, price, min_level, set_name, rarity))
        art_id = cursor.lastrowid

        # Сохраняем картинку под именем ID.png (например, 15.png)
        if image_file and image_file.filename != '':
            filepath = os.path.join('static', 'img', 'artifacts', f"{art_id}.png")
            image_file.save(filepath)

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/edit_artifact', methods=['POST'])
def edit_artifact():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403

    art_id = request.form.get('id')
    name = request.form.get('name')
    set_name = request.form.get('set_name')
    rarity = request.form.get('rarity')
    price = int(request.form.get('price'))
    min_level = int(request.form.get('min_level'))
    image_file = request.files.get('image')

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            UPDATE artifacts 
            SET name=%s, set_name=%s, rarity=%s, price=%s, min_level=%s 
            WHERE id=%s
        ''', (name, set_name, rarity, price, min_level, art_id))

        if image_file and image_file.filename != '':
            filepath = os.path.join('static', 'img', 'artifacts', f"{art_id}.png")
            image_file.save(filepath)

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/api/delete_artifact', methods=['POST'])
def delete_artifact():
    if 'user_id' not in session or session['role'] != 'teacher': return jsonify(
        {'status': 'error'}), 403
    art_id = request.json.get('id')
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Сначала удаляем артефакт из витрин магазина и инвентарей учеников, чтобы база не ругалась
        cursor.execute('DELETE FROM user_shop_slots WHERE artifact_id = %s', (art_id,))
        cursor.execute('DELETE FROM inventory WHERE artifact_id = %s', (art_id,))
        # Удаляем сам артефакт
        cursor.execute('DELETE FROM artifacts WHERE id = %s', (art_id,))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        conn.close()


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


if __name__ == '__main__':
    app.run(debug=True)
