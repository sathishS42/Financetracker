from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import io, csv

app = Flask(__name__)
app.secret_key = 'dev-secret-change-me'

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def init_db():
    conn = sqlite3.connect('expenses.db')
    c = conn.cursor()
    
    # Drop existing tables
    c.execute('DROP TABLE IF EXISTS transactions')
    
    # Create tables
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                  
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  description TEXT NOT NULL,
                  amount REAL NOT NULL,
                  type TEXT NOT NULL,
                  category TEXT NOT NULL,
                  date TEXT NOT NULL,
                  user_id INTEGER NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('signup'))
    return render_template('index.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return render_template('signup.html', error='Username and password required')
        conn = sqlite3.connect('expenses.db')
        c = conn.cursor()
        try:
            hashed = generate_password_hash(password)
            c.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('signup.html', error='Username already taken')
        conn.close()
        session['username'] = username
        return redirect(url_for('index'))
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = sqlite3.connect('expenses.db')
        c = conn.cursor()
        c.execute('SELECT id, password FROM users WHERE username = ?', (username,))
        row = c.fetchone()
        conn.close()
        if row and check_password_hash(row[1], password):
            session['username'] = username
            return redirect(url_for('index'))
        return render_template('login.html', error='Invalid username or password')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))


@app.route('/download/csv')
@login_required
def download_csv():
    # Export transactions for current user to CSV
    conn = sqlite3.connect('expenses.db')
    c = conn.cursor()
    c.execute('SELECT id, description, amount, type, category, date, created_at FROM transactions WHERE user_id = (SELECT id FROM users WHERE username = ?) ORDER BY date DESC', (session['username'],))
    rows = c.fetchall()
    conn.close()

    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(['id', 'description', 'amount', 'type', 'category', 'date', 'created_at'])
    for r in rows:
        writer.writerow(r)

    output = make_response(si.getvalue())
    output.headers['Content-Disposition'] = 'attachment; filename=transactions.csv'
    output.headers['Content-type'] = 'text/csv; charset=utf-8'
    return output

@app.route('/api/transactions', methods=['GET'])
@login_required
def get_transactions():
    conn = sqlite3.connect('expenses.db')
    c = conn.cursor()
    c.execute('SELECT * FROM transactions WHERE user_id = (SELECT id FROM users WHERE username = ?) ORDER BY date DESC', (session['username'],))
    transactions = []
    for row in c.fetchall():
        transactions.append({
            'id': row[0],
            'description': row[1],
            'amount': row[2],
            'type': row[3],
            'category': row[4],
            'date': row[5]
        })
    conn.close()
    return jsonify(transactions)

@app.route('/api/transactions', methods=['POST'])
@login_required
def add_transaction():
    try:
        data = request.json
        conn = sqlite3.connect('expenses.db')
        c = conn.cursor()
        
        # Get user_id from username
        c.execute('SELECT id FROM users WHERE username = ?', (session['username'],))
        user_row = c.fetchone()
        if not user_row:
            conn.close()
            return jsonify({'success': False, 'error': 'User not found'}), 400
        
        user_id = user_row[0]
        
        # Validate required fields
        required_fields = ['description', 'amount', 'type', 'category', 'date']
        for field in required_fields:
            if field not in data:
                conn.close()
                return jsonify({'success': False, 'error': f'Missing {field}'}), 400
        
        c.execute('''INSERT INTO transactions 
                     (description, amount, type, category, date, user_id)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (data['description'], data['amount'], data['type'], 
                   data['category'], data['date'], user_id))
        
        conn.commit()
        transaction_id = c.lastrowid
        conn.close()
        
        return jsonify({
            'success': True,
            'id': transaction_id,
            'message': 'Transaction added successfully'
        })
        
    except Exception as e:
        print(f"Error adding transaction: {str(e)}")  # For debugging
        return jsonify({
            'success': False,
            'error': 'Failed to add transaction'
        }), 500

@app.route('/api/transactions/<int:id>', methods=['DELETE'])
@login_required
def delete_transaction(id):
    conn = sqlite3.connect('expenses.db')
    c = conn.cursor()
    # Only delete if transaction belongs to current user
    c.execute('DELETE FROM transactions WHERE id = ? AND user_id = (SELECT id FROM users WHERE username = ?)', 
             (id, session['username']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/statistics/<month>', methods=['GET'])
@login_required
def get_statistics(month):
    conn = sqlite3.connect('expenses.db')
    c = conn.cursor()
    
    c.execute('''SELECT type, SUM(amount) FROM transactions 
                 WHERE date LIKE ? AND user_id = (SELECT id FROM users WHERE username = ?)
                 GROUP BY type''', (f'{month}%', session['username']))
    totals = {'income': 0, 'expense': 0}
    for row in c.fetchall():
        totals[row[0]] = row[1]
    
    c.execute('''SELECT category, SUM(amount) FROM transactions 
                 WHERE type = "expense" AND date LIKE ? AND user_id = (SELECT id FROM users WHERE username = ?)
                 GROUP BY category''', (f'{month}%', session['username']))
    categories = [{'name': row[0], 'value': row[1]} for row in c.fetchall()]
    
    c.execute('''SELECT date, SUM(amount) FROM transactions 
                 WHERE type = "expense" AND date LIKE ? AND user_id = (SELECT id FROM users WHERE username = ?)
                 GROUP BY date ORDER BY date''', (f'{month}%', session['username']))
    daily = [{'date': row[0], 'amount': row[1]} for row in c.fetchall()]
    
    c.execute('''SELECT substr(date, 1, 7) as month, type, SUM(amount) 
                 FROM transactions WHERE user_id = (SELECT id FROM users WHERE username = ?)
                 GROUP BY month, type ORDER BY month''', (session['username'],))
    monthly = {}
    for row in c.fetchall():
        if row[0] not in monthly:
            monthly[row[0]] = {'month': row[0], 'income': 0, 'expense': 0}
        monthly[row[0]][row[1]] = row[2]
    
    conn.close()
    return jsonify({
        'totals': totals,
        'categories': categories,
        'daily': daily,
        'monthly': list(monthly.values())
    })

if __name__ == '__main__':
    app.run(debug=True)