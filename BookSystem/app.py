import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash
import datetime

app = Flask(__name__)
app.secret_key = 'super_secret_key_for_students'

# ================= 数据库连接 =================
def get_db_connection():
    conn = psycopg2.connect(
        host="10.10.192.144",
        database="bookdb",
        user="book_app",
        password="App_Password@123",
        port="5432"
    )
    return conn

# ================= 首页 =================
@app.route('/')
def index():
    query = request.args.get('q', '')
    conn = get_db_connection()
    cur = conn.cursor()
    
    if query:
        cur.execute("""
            SELECT isbn, book_title, book_author, year_of_publication, image_url_l 
            FROM books 
            WHERE book_title ILIKE %s 
               OR isbn ILIKE %s 
               OR book_author ILIKE %s 
               OR publisher ILIKE %s 
            LIMIT 50
        """, ('%' + query + '%', '%' + query + '%', '%' + query + '%', '%' + query + '%'))
    else:
        # 显示评分最高的16本书
        cur.execute("""
            SELECT b.isbn, b.book_title, b.book_author, b.year_of_publication, b.image_url_l 
            FROM books b
            LEFT JOIN (
                SELECT isbn, ROUND(AVG(book_rating), 1) as avg_rating, COUNT(*) as rating_count
                FROM ratings
                GROUP BY isbn
            ) r ON b.isbn = r.isbn
            WHERE r.rating_count >= 15
            ORDER BY r.avg_rating DESC, r.rating_count DESC
            LIMIT 12
        """)
        
    books = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('index_material.html', books=books, search_query=query)

# ================= 登录 / 退出 =================
@app.route('/login', methods=['GET', 'POST'])
def login():
    next_page = request.args.get('next')
        
    if request.method == 'POST':
        user_id = request.form['user_id']
        password = request.form['password']
    
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT role FROM user_accounts 
            WHERE user_id=%s AND password=%s
        """, (user_id, password))
        account = cur.fetchone()
        cur.close()
        conn.close()
        
        if account:
            session['user_id'] = user_id
            session['role'] = account[0]
            flash('登录成功！', 'success')
            return redirect(next_page or url_for('index'))
        else:
            flash('账号或密码错误', 'danger')
            
    return render_template('login_material.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ================= 书籍详情 =================
@app.route('/book/<isbn>', methods=['GET', 'POST'])
def book_detail(isbn):
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST' and 'user_id' in session:
        action = request.form.get('action')
        uid = session['user_id']
        
        if action == 'rate':
            score = int(request.form.get('rating'))
            cur.execute("DELETE FROM ratings WHERE user_id=%s AND isbn=%s", (uid, isbn))
            cur.execute("""
                INSERT INTO ratings (user_id, isbn, book_rating)
                VALUES (%s, %s, %s)
            """, (uid, isbn, score))
            conn.commit()
            flash('评分已更新！', 'success')
            
        elif action == 'wishlist':
            # 检查是否已存在
            cur.execute("""
                SELECT 1 FROM appealing_books 
                WHERE user_id=%s AND isbn=%s
            """, (uid, isbn))
            exists = cur.fetchone()
            
            if exists:
                # 已存在，删除（取消想看）
                cur.execute("""
                    DELETE FROM appealing_books 
                    WHERE user_id=%s AND isbn=%s
                """, (uid, isbn))
                conn.commit()
                flash('已取消想看', 'info')
            else:
                # 不存在，添加
                cur.execute("""
                    INSERT INTO appealing_books (user_id, isbn)
                    VALUES (%s, %s)
                """, (uid, isbn))
                conn.commit()
                flash('已加入想看的书单', 'success')

        elif action == 'request_edit':
            content = request.form.get('content')
            cur.execute("""
                INSERT INTO book_requests (user_id, isbn, request_type, content, status)
                VALUES (%s, %s, 'MODIFY', %s, 'PENDING')
            """, (uid, isbn, content))
            conn.commit()
            flash('修改申请已提交', 'info')

    cur.execute("SELECT * FROM books WHERE isbn=%s", (isbn,))
    book = cur.fetchone()

    cur.execute("SELECT ROUND(AVG(book_rating),1) FROM ratings WHERE isbn=%s", (isbn,))
    avg_rating = cur.fetchone()[0] or '暂无'

    my_rating = None
    in_wishlist = False
    if 'user_id' in session:
        cur.execute("""
            SELECT book_rating FROM ratings
            WHERE user_id=%s AND isbn=%s
        """, (session['user_id'], isbn))
        r = cur.fetchone()
        if r:
            my_rating = r[0]
        
        # 检查是否已加入想看
        cur.execute("""
            SELECT 1 FROM appealing_books
            WHERE user_id=%s AND isbn=%s
        """, (session['user_id'], isbn))
        in_wishlist = cur.fetchone() is not None

    cur.close()
    conn.close()
    return render_template(
        'book_detail_material.html',
        book=book,
        avg_rating=avg_rating,
        my_rating=my_rating,
        in_wishlist=in_wishlist
    )

# =============== 管理员部分 ====================

@app.route('/admin')
def admin():
    if session.get('role') != 'admin':
        return "无权访问", 403
    return render_template('admin.html')

@app.route('/admin/books')
def admin_books():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    keyword = request.args.get('keyword', '')
    field = request.args.get('field', 'all')

    conn = get_db_connection()
    cur = conn.cursor()

    sql = "SELECT * FROM books"
    params = []

    if keyword:
        if field == 'isbn':
            sql += " WHERE isbn ILIKE %s"
            params.append('%'+keyword+'%')
        elif field == 'title':
            sql += " WHERE book_title ILIKE %s"
            params.append('%'+keyword+'%')
        elif field == 'author':
            sql += " WHERE book_author ILIKE %s"
            params.append('%'+keyword+'%')
        else:
            sql += """
                WHERE isbn ILIKE %s OR
                      book_title ILIKE %s OR
                      book_author ILIKE %s
            """
            params += ['%'+keyword+'%'] * 3
    
    # 添加限制，避免加载过多数据
    sql += " LIMIT 100"

    cur.execute(sql, params)
    books = cur.fetchall()
    cur.close()
    conn.close()

    return render_template(
        'admin_books.html',
        books=books,
        keyword=keyword,
        field=field
    )

@app.route('/admin/book/edit/<isbn>', methods=['GET', 'POST'])
def admin_book_edit(isbn):
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        cur.execute("""
            UPDATE books
            SET book_title=%s,
                book_author=%s,
                year_of_publication=%s,
                publisher=%s
            WHERE isbn=%s
        """, (
            request.form['title'],
            request.form['author'],
            request.form['year'],
            request.form['publisher'],
            isbn
        ))
        
        # 记录到book_requests表
        cur.execute("""
            INSERT INTO book_requests (user_id, isbn, request_type, content, status, processed_by, processed_at)
            VALUES (%s, %s, 'MODIFY', '管理员直接编辑', 'APPROVED', %s, CURRENT_TIMESTAMP)
        """, (session.get('user_id'), isbn, session.get('user_id')))
        
        conn.commit()
        cur.close()
        conn.close()
        flash('图书信息已更新', 'success')
        return redirect(url_for('admin_books'))

    cur.execute("SELECT * FROM books WHERE isbn=%s", (isbn,))
    book = cur.fetchone()
    cur.close()
    conn.close()
    return render_template('admin_book_edit.html', book=book)

@app.route('/admin/book/delete/<isbn>', methods=['POST'])
def admin_book_delete(isbn):
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    # 记录到book_requests表（删除前记录）
    cur.execute("""
        INSERT INTO book_requests (user_id, isbn, request_type, content, status, processed_by, processed_at)
        VALUES (%s, %s, 'DELETE', '管理员删除图书', 'APPROVED', %s, CURRENT_TIMESTAMP)
    """, (session.get('user_id'), isbn, session.get('user_id')))
    
    cur.execute("DELETE FROM books WHERE isbn=%s", (isbn,))
    conn.commit()
    cur.close()
    conn.close()
    flash('图书已删除', 'warning')
    return redirect(url_for('admin_books'))

@app.route('/admin/recent-books')
def admin_recent_books():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    # 查询最近的操作记录（包括编辑和删除）
    cur.execute("""
        SELECT DISTINCT ON (r.isbn)
               r.isbn,
               COALESCE(b.book_title, '已删除'),
               COALESCE(b.book_author, '-'),
               r.request_type,
               r.processed_at
        FROM book_requests r
        LEFT JOIN books b ON r.isbn = b.isbn
        WHERE r.request_type IN ('MODIFY', 'DELETE')
          AND r.status = 'APPROVED'
        ORDER BY r.isbn, r.processed_at DESC
        LIMIT 50
    """)
    operations = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('admin_books_recent.html', operations=operations)

@app.route('/admin/requests/<status>')
def admin_requests(status):
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM book_requests
        WHERE status=%s
        ORDER BY created_at DESC
    """, (status.upper(),))
    requests = cur.fetchall()
    cur.close()
    conn.close()

    return render_template(
        'admin_requests.html',
        requests=requests,
        status=status.upper()
    )

# ================= 批准申请 =================
@app.route('/admin/request/approve/<int:request_id>', methods=['POST'])
def approve_request(request_id):
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE book_requests
        SET status='APPROVED',
            processed_by=%s,
            processed_at=CURRENT_TIMESTAMP
        WHERE request_id=%s
    """, (session.get('user_id'), request_id))
    conn.commit()
    cur.close()
    conn.close()
    
    flash('申请已批准', 'success')
    return redirect(url_for('admin_requests', status='pending'))

# ================= 驳回申请 =================
@app.route('/admin/request/reject/<int:request_id>', methods=['POST'])
def reject_request(request_id):
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE book_requests
        SET status='REJECTED',
            processed_by=%s,
            processed_at=CURRENT_TIMESTAMP
        WHERE request_id=%s
    """, (session.get('user_id'), request_id))
    conn.commit()
    cur.close()
    conn.close()
    
    flash('申请已驳回', 'info')
    return redirect(url_for('admin_requests', status='pending'))

# ================= 添加图书 =================
@app.route('/admin/book/add', methods=['GET', 'POST'])
def admin_book_add():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        isbn = request.form['isbn']
        title = request.form['title']
        author = request.form['author']
        year = request.form['year']
        publisher = request.form['publisher']
        image_url = request.form['image_url_m']

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO books (isbn, book_title, book_author, year_of_publication, publisher, image_url_m)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (isbn, title, author, year, publisher, image_url))
        conn.commit()
        cur.close()
        conn.close()

        flash('图书已添加', 'success')
        return redirect(url_for('admin_books'))

    # GET 请求显示页面
    return render_template('admin_book_add.html')


# ================= 用户个人主页 =================
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        flash('请先登录', 'warning')
        return redirect(url_for('login', next=url_for('profile')))

    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT user_id, location, age FROM users WHERE user_id=%s", (user_id,))
    user_info = cur.fetchone()

    cur.execute("""
        SELECT b.isbn, b.book_title, r.book_rating
        FROM ratings r
        JOIN books b ON r.isbn = b.isbn
        WHERE r.user_id=%s
    """, (user_id,))
    ratings = cur.fetchall()

    cur.execute("""
        SELECT b.isbn, b.book_title
        FROM appealing_books a
        JOIN books b ON a.isbn = b.isbn
        WHERE a.user_id=%s
    """, (user_id,))
    wishlist = cur.fetchall()

    cur.close()
    conn.close()
    return render_template(
        'profile_material.html',
        user_info=user_info,
        ratings=ratings,
        wishlist=wishlist
    )

@app.route('/profile/edit', methods=['GET', 'POST'])
def edit_profile():
    if 'user_id' not in session:
        flash('请先登录', 'warning')
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        location = request.form.get('location', '').strip()
        age = request.form.get('age', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        current_password = request.form.get('current_password', '').strip()
        
        # 验证当前密码
        cur.execute("SELECT password FROM user_accounts WHERE user_id=%s", (user_id,))
        account = cur.fetchone()
        
        if not account or account[0] != current_password:
            flash('当前密码错误', 'danger')
            cur.close()
            conn.close()
            return redirect(url_for('edit_profile'))
        
        # 如果要修改密码，检查新密码
        if new_password:
            if new_password != confirm_password:
                flash('两次输入的新密码不一致', 'danger')
                cur.close()
                conn.close()
                return redirect(url_for('edit_profile'))
            
            if len(new_password) < 4:
                flash('密码长度至少4个字符', 'danger')
                cur.close()
                conn.close()
                return redirect(url_for('edit_profile'))
            
            # 更新密码
            cur.execute("UPDATE user_accounts SET password=%s WHERE user_id=%s", 
                       (new_password, user_id))
        
        # 更新用户信息
        age_val = int(age) if age and age.isdigit() else None
        cur.execute("""
            UPDATE users 
            SET location=%s, age=%s 
            WHERE user_id=%s
        """, (location if location else None, age_val, user_id))
        
        conn.commit()
        cur.close()
        conn.close()
        
        flash('个人信息修改成功！', 'success')
        return redirect(url_for('profile'))
    
    # GET请求，显示表单
    cur.execute("SELECT user_id, location, age FROM users WHERE user_id=%s", (user_id,))
    user_info = cur.fetchone()
    cur.close()
    conn.close()
    
    return render_template('edit_profile_material.html', user_info=user_info)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
