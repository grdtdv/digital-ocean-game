/* ==========================================
   1. ОБЩИЕ ФУНКЦИИ (UI)
   ========================================== */
function navigateTo(pageId) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const target = document.getElementById(pageId);
    if(target) target.classList.add('active');
}

function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.add('active');
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('active');
}

/* ==========================================
   2. ВХОД
   ========================================== */
function toggleUserType(type) {
    const btns = document.querySelectorAll('.toggle-switch .btn');
    btns.forEach(b => b.classList.remove('active'));
    const activeBtn = Array.from(btns).find(b => b.textContent.toLowerCase().includes(type === 'student' ? 'ученик' : 'учитель'));
    if(activeBtn) activeBtn.classList.add('active');

    if (type === 'student') {
        document.getElementById('studentLoginForm').classList.remove('hidden');
        document.getElementById('teacherLoginForm').classList.add('hidden');
    } else {
        document.getElementById('studentLoginForm').classList.add('hidden');
        document.getElementById('teacherLoginForm').classList.remove('hidden');
    }
}

function selectGender(gender) {
    const btns = document.querySelectorAll('#studentLoginForm .toggle-switch .btn');
    btns.forEach(b => b.classList.remove('active'));
    const clickedBtn = document.querySelector(`button[onclick="selectGender('${gender}')"]`);
    if(clickedBtn) clickedBtn.classList.add('active');
    document.getElementById('genderInput').value = gender;
}

/* ==========================================
   3. УЧЕНИК (МАГАЗИН, СЕТЫ, МОНСТРЫ)
   ========================================== */
function filterShop(type) {
    const btns = document.querySelectorAll('.filter-buttons .btn');
    btns.forEach(btn => {
        btn.classList.remove('btn--primary');
        btn.classList.add('btn--secondary');
    });
    event.target.classList.remove('btn--secondary');
    event.target.classList.add('btn--primary');
}

function buyArtifact(artifactId) {
    if(!confirm('Купить этот предмет?')) return;
    fetch('/api/buy_artifact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artifact_id: artifactId })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') { alert('Куплено!'); location.reload(); } 
        else { alert('Ошибка: ' + data.message); }
    });
}

function helpMonster() {
    const points = parseInt(document.getElementById('helpPoints').value);
    if (!points || points <= 0) { alert('Введите баллы'); return; }
    fetch('/api/attack_monster', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: points })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') { alert('Урон нанесен!'); location.reload(); }
        else { alert('Ошибка: ' + data.message); }
    });
}

function activateSet(setName) {
    if(!confirm(`Активировать "${setName}"? Учителю будет отправлен запрос.`)) return;
    fetch('/api/request_set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ set_name: setName })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') { alert('Заявка отправлена учителю!'); location.reload(); } 
        else { alert('Ошибка: ' + data.message); }
    });
}

/* ==========================================
   4. УЧИТЕЛЬ (НАЧИСЛЕНИЕ БАЛЛОВ И СЕТЫ)
   ========================================== */
let currentStudentIds =[]; 

const ballTable = {
    'controlWork': { '5': 100, '4': 75, '3': 50, '2': 0 },
    'independentWork': { '5': 50, '4': 40, '3': 25, '2': 0 },
    'homework': { '5': 10, '4': 7, '3': 5, '2': 0 },
    'additional': 'manual'
};

function toggleSelectAll(source) {
    const checkboxes = document.querySelectorAll('.student-checkbox');
    checkboxes.forEach(cb => { cb.checked = source.checked; });
    updateBulkButtonState();
}

function updateBulkButtonState() {
    const checkedBoxes = document.querySelectorAll('.student-checkbox:checked');
    const bulkBtn = document.getElementById('bulkPointsBtn');
    if(bulkBtn) bulkBtn.disabled = checkedBoxes.length === 0;
}

function openBulkPointsModal() {
    const checkedBoxes = document.querySelectorAll('.student-checkbox:checked');
    currentStudentIds = Array.from(checkedBoxes).map(cb => cb.value);
    document.getElementById('selectedStudentName').textContent = `Выбрано учеников: ${currentStudentIds.length}`;
    resetModalFields();
    openModal('addPointsModal');
}

function openAddPointsModal(studentId, studentName) {
    currentStudentIds = [studentId]; 
    document.getElementById('selectedStudentName').textContent = `Ученик: ${studentName}`;
    resetModalFields();
    openModal('addPointsModal');
}

function resetModalFields() {
    document.getElementById('workType').value = 'controlWork';
    document.getElementById('grade').value = '5';
    updatePointsPreview();
}

function updatePointsPreview() {
    const workType = document.getElementById('workType').value;
    const grade = document.getElementById('grade').value;
    const manualInput = document.getElementById('manualPointsInput');
    const preview = document.getElementById('pointsPreview');
    
    let points = 0;
    if (workType === 'additional') {
        if (manualInput) manualInput.classList.remove('hidden');
        points = document.getElementById('manualPoints') ? document.getElementById('manualPoints').value : 0;
    } else {
        if (manualInput) manualInput.classList.add('hidden');
        if (ballTable[workType] && ballTable[workType][grade] !== undefined) {
            points = ballTable[workType][grade];
        }
    }
    if (preview) preview.textContent = `Будет начислено: ${points} баллов каждому`;
}

function confirmAddPoints() {
    const workType = document.getElementById('workType').value;
    let points = 0;
    if (workType === 'additional') {
        points = parseInt(document.getElementById('manualPoints').value);
    } else {
        const grade = document.getElementById('grade').value;
        points = ballTable[workType][grade];
    }

    if (currentStudentIds.length === 0) { alert("Ошибка: Никто не выбран!"); return; }
    if (isNaN(points) || points <= 0) { alert("Ошибка: Некорректное количество баллов!"); return; }

    fetch('/api/give_points', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ student_ids: currentStudentIds, amount: points, reason: workType })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') { alert('Баллы успешно начислены!'); location.reload(); }
        else { alert('Ошибка: ' + data.message); }
    });
}

function approveSet(requestId) {
    fetch('/api/approve_set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: requestId })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') { alert('Сет успешно активирован!'); location.reload(); } 
        else { alert('Ошибка: ' + data.message); }
    });
}

/* ==========================================
   5. УЧИТЕЛЬ (УПРАВЛЕНИЕ КЛАССОМ И МОНСТРАМИ)
   ========================================== */

function openMonsterAttackModal() {
    const attackInput = document.getElementById('attackDamage');
    if (attackInput) attackInput.value = '10'; 
    openModal('monsterAttackModal');
}

function confirmMonsterAttack() {
    const damage = parseInt(document.getElementById('attackDamage').value);
    
    if (isNaN(damage) || damage <= 0) {
        alert("Введите корректное число баллов урона!");
        return;
    }

    if (!confirm(`Монстр спишет ${damage} баллов у ВСЕХ учеников. Вы уверены?`)) {
        return;
    }

    fetch('/api/monster_attack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: damage })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            alert(`Монстр атаковал! У всего класса списано по ${damage} баллов.`);
            location.reload(); 
        } else {
            alert('Ошибка сервера: ' + data.message);
        }
    });
}


// --- ЗАВЕРШИТЬ УРОВЕНЬ МОНСТРА ---
function openCompleteMonsterModal() {
    openModal('completeMonsterModal');
}

function completeMonster() {
    fetch('/api/complete_monster', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            alert('Уровень завершен! Переходим к следующему монстру.');
            location.reload(); 
        } else {
            alert('Ошибка: ' + data.message);
        }
    });
}

// --- ПЕРЕВЕСТИ КЛАСС НА НОВЫЙ УРОВЕНЬ ---
function levelUpClass() {
    if (!confirm("Вы действительно хотите перевести весь класс на новый уровень? У всех учеников уровень повысится на +1.")) {
        return;
    }

    fetch('/api/levelup_class', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            alert('Класс успешно переведен на следующий уровень!');
            location.reload(); 
        } else {
            alert('Ошибка сервера: ' + data.message);
        }
    });
}