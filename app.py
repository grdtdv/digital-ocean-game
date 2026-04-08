from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import mysql.connector

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


# --- ЛОГИН ---
@app.route('/', methods=['GET', 'POST'])
def login():
    msg = ''
    if request.method == 'POST':
        role = request.form.get('role')
        username = request.form.get('username')
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        if role == 'teacher':
            password = request.form.get('password')
            cursor.execute(
                'SELECT * FROM users WHERE username = %s AND password = %s AND role="teacher"',
                (username, password))
            user = cursor.fetchone()
        else:
            cursor.execute('SELECT * FROM users WHERE username = %s AND role="student"', (username,))
            user = cursor.fetchone()
            if not user:
                gender = request.form.get('gender', 'boy')
                cursor.execute(
                    'INSERT INTO users (username, password, role, avatar_type, full_name) VALUES (%s, "", "student", %s, %s)',
                    (username, gender, username))
                user_id = cursor.lastrowid
                cursor.execute('INSERT INTO student_progress (user_id) VALUES (%s)', (user_id,))
                conn.commit()
                cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
                user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user:
            session['loggedin'] = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name']
            return redirect('/teacher' if user['role'] == 'teacher' else '/student')
        else:
            msg = 'Ошибка входа'
    return render_template('login.html', msg=msg)


# --- КАБИНЕТ УЧЕНИКА ---
@app.route('/student')
def student_dashboard():
    if 'user_id' not in session or session['role'] != 'student': return redirect('/')

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute('SELECT * FROM student_progress WHERE user_id = %s', (session['user_id'],))
    student_data = cursor.fetchone()

    cursor.execute('SELECT * FROM monsters WHERE is_active = TRUE LIMIT 1')
    monster = cursor.fetchone()
    if not monster:  # На случай, если в базе пусто
        monster = {'name': 'Нет монстра', 'current_hp': 0, 'max_hp': 100, 'quarter': 0}

    cursor.execute('SELECT * FROM artifacts')
    artifacts = cursor.fetchall()

    cursor.execute('SELECT artifact_id FROM inventory WHERE user_id = %s', (session['user_id'],))
    inventory_ids = [row['artifact_id'] for row in cursor.fetchall()]

    cursor.execute('''
        SELECT a.* FROM artifacts a
        JOIN inventory i ON a.id = i.artifact_id
        WHERE i.user_id = %s
    ''', (session['user_id'],))
    my_inventory = cursor.fetchall()

    # --- ЛОГИКА СЕТОВ ---
    # 1. Считаем, сколько всего предметов в каждом сете существует
    cursor.execute('SELECT set_name, COUNT(*) as total_items FROM artifacts GROUP BY set_name')
    set_requirements = {row['set_name']: row['total_items'] for row in cursor.fetchall()}

    # 2. Считаем, сколько предметов из сета купил ученик
    cursor.execute('''
        SELECT a.set_name, COUNT(i.id) as owned_items 
        FROM artifacts a 
        JOIN inventory i ON a.id = i.artifact_id 
        WHERE i.user_id = %s 
        GROUP BY a.set_name
    ''', (session['user_id'],))
    owned_sets_info = {row['set_name']: row['owned_items'] for row in cursor.fetchall()}

    # 3. Проверяем статусы заявок на сеты
    cursor.execute('SELECT set_name, status FROM set_requests WHERE student_id = %s',
                   (session['user_id'],))
    requests_info = {row['set_name']: row['status'] for row in cursor.fetchall()}

    # Собираем финальный массив сетов для HTML
    sets_data = []
    for set_name, total in set_requirements.items():
        owned = owned_sets_info.get(set_name, 0)
        is_complete = (owned == total)
        status = requests_info.get(set_name, None)

        sets_data.append({
            'name': set_name,
            'is_complete': is_complete,
            'status': status,
            'owned': owned,
            'total': total
        })
    # --------------------

    conn.close()

    return render_template('student.html',
                           student=student_data,
                           monster=monster,
                           artifacts=artifacts,
                           inventory_ids=inventory_ids,
                           my_inventory=my_inventory,
                           sets_data=sets_data)  # Передаем сеты в шаблон


# --- КАБИНЕТ УЧИТЕЛЯ ---
@app.route('/teacher')
def teacher_dashboard():
    if 'user_id' not in session or session['role'] != 'teacher': return redirect('/')
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        'SELECT u.id, u.full_name, sp.current_points FROM users u JOIN student_progress sp ON u.id = sp.user_id WHERE u.role = "student"')
    students = cursor.fetchall()

    # --- ЗАЯВКИ НА СЕТЫ ---
    cursor.execute('''
        SELECT r.id, r.set_name, u.full_name 
        FROM set_requests r 
        JOIN users u ON r.student_id = u.id 
        WHERE r.status = 'pending'
    ''')
    pending_requests = cursor.fetchall()
    # ----------------------

    conn.close()
    return render_template('teacher.html', students=students, pending_requests=pending_requests)


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


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


if __name__ == '__main__':
    app.run(debug=True)
