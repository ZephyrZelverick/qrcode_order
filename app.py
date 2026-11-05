from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
from sqlalchemy import text
import qrcode
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key_for_restaurant'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# ฐานข้อมูลเมนูอาหาร
class MenuItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    is_available = db.Column(db.Boolean, default=True)

# ฐานข้อมูลออเดอร์
class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    queue_number = db.Column(db.Integer, nullable=False)
    customer_name = db.Column(db.String(100), nullable=False)
    order_type = db.Column(db.String(20), nullable=False) # dine_in / takeaway
    table_number = db.Column(db.String(20), nullable=True)
    details = db.Column(db.Text, nullable=False)
    note = db.Column(db.Text, nullable=True)
    total_price = db.Column(db.Float, default=0.0) # ราคารวม
    status = db.Column(db.String(20), default='pending') # pending / served / cleared / cancelled
    payment_method = db.Column(db.String(20), default='cash') # cash / promptpay
    payment_status = db.Column(db.String(20), default='pending') # pending / paid

with app.app_context():
    db.create_all()
    try:
        db.session.execute(text("ALTER TABLE 'order' ADD COLUMN total_price FLOAT DEFAULT 0.0"))
    except Exception:
        db.session.rollback()
    try:
        db.session.execute(text("ALTER TABLE 'order' ADD COLUMN payment_method VARCHAR(20) DEFAULT 'cash'"))
        db.session.execute(text("ALTER TABLE 'order' ADD COLUMN payment_status VARCHAR(20) DEFAULT 'pending'"))
        db.session.commit()
    except Exception:
        db.session.rollback()

# หน้าหลักสำหรับลูกค้า
@app.route('/')
def customer_index():
    items = MenuItem.query.filter_by(is_available=True).all()
    queue = request.args.get('queue')
    last_order = None
    if queue:
        last_order = Order.query.filter_by(queue_number=int(queue)).order_by(Order.id.desc()).first()
    return render_template('customer.html', items=items, queue=queue, last_order=last_order)

# ระบบรับออเดอร์จากลูกค้า
@app.route('/submit-order', methods=['POST'])
def submit_order():
    customer_name = request.form.get('customer_name')
    order_type = request.form.get('order_type')
    table_number = request.form.get('table_number') if order_type == 'dine_in' else None
    note = request.form.get('note')
    payment_method = request.form.get('payment_method', 'cash')

    items = MenuItem.query.filter_by(is_available=True).all()
    order_details = []
    total_price = 0.0

    for item in items:
        qty = int(request.form.get(f'item_{item.id}', 0))
        if qty > 0:
            add_egg = request.form.get(f'egg_{item.id}') == '1'
            item_price = item.price + (10.0 if add_egg else 0.0)
            subtotal = item_price * qty
            total_price += subtotal
            
            egg_text = " (+🍳 เพิ่มไข่ดาว)" if add_egg else ""
            order_details.append(f"{item.name} x {qty}{egg_text}")

    if order_details:
        last_order = Order.query.order_by(Order.id.desc()).first()
        queue_num = (last_order.queue_number + 1) if last_order else 1

        new_order = Order(
            queue_number=queue_num,
            customer_name=customer_name,
            order_type=order_type,
            table_number=table_number,
            details=", ".join(order_details),
            note=note,
            total_price=total_price,
            payment_method=payment_method,
            payment_status='pending'
        )
        db.session.add(new_order)
        db.session.commit()
        
        socketio.emit('new_order_event', {'message': 'New Order!'})
        return redirect(url_for('customer_index', queue=queue_num))

    return redirect(url_for('customer_index'))

# หน้าต่างสำหรับแม่ค้า
@app.route('/merchant')
def merchant_index():
    orders = Order.query.filter_by(status='pending').order_by(Order.queue_number.asc()).all()
    history_orders = Order.query.filter(Order.status.in_(['served', 'cleared', 'cancelled'])).order_by(Order.id.desc()).all()
    items = MenuItem.query.all()
    
    table_orders = {}
    table_totals = {}
    active_orders = Order.query.filter(Order.order_type == 'dine_in', Order.status.in_(['pending', 'served'])).all()
    
    for o in active_orders:
        if o.table_number:
            if o.table_number not in table_orders:
                table_orders[o.table_number] = []
                table_totals[o.table_number] = 0.0
            table_orders[o.table_number].append(o)
            table_totals[o.table_number] += o.total_price
            
    active_tables = list(table_orders.keys())

    return render_template('merchant.html', 
                           orders=orders, 
                           history_orders=history_orders, 
                           items=items, 
                           active_tables=active_tables,
                           table_orders=table_orders,
                           table_totals=table_totals)

@app.route('/toggle-payment/<int:order_id>', methods=['POST'])
def toggle_payment(order_id):
    order = Order.query.get_or_404(order_id)
    order.payment_status = 'paid' if order.payment_status == 'pending' else 'pending'
    db.session.commit()
    socketio.emit('order_updated_event', {'message': 'Payment Status Updated'})
    return redirect(url_for('merchant_index'))

@app.route('/add-item', methods=['POST'])
def add_item():
    name = request.form.get('name')
    price = float(request.form.get('price', 0))
    if name and price:
        new_item = MenuItem(name=name, price=price)
        db.session.add(new_item)
        db.session.commit()
    return redirect(url_for('merchant_index'))

@app.route('/delete-item/<int:item_id>', methods=['POST'])
def delete_item(item_id):
    item = MenuItem.query.get(item_id)
    if item:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for('merchant_index'))

@app.route('/toggle-item/<int:item_id>', methods=['POST'])
def toggle_item(item_id):
    item = MenuItem.query.get(item_id)
    if item:
        item.is_available = not item.is_available
        db.session.commit()
    return redirect(url_for('merchant_index'))

@app.route('/complete-order/<int:order_id>', methods=['POST'])
def complete_order(order_id):
    order = Order.query.get(order_id)
    if order:
        order.status = 'served'
        db.session.commit()
        socketio.emit('order_updated_event', {'message': 'Order Updated'})
    return redirect(url_for('merchant_index'))

@app.route('/clear-table/<table_num>', methods=['POST'])
def clear_table(table_num):
    orders = Order.query.filter_by(table_number=table_num, order_type='dine_in').all()
    for o in orders:
        o.status = 'cleared'
    db.session.commit()
    socketio.emit('order_updated_event', {'message': 'Table Cleared'})
    return redirect(url_for('merchant_index'))

@app.route('/reset-queue', methods=['POST'])
def reset_queue():
    Order.query.delete()
    db.session.commit()
    socketio.emit('order_updated_event', {'message': 'Queue Reset'})
    return redirect(url_for('merchant_index'))

@app.route('/cancel-order/<int:order_id>', methods=['POST'])
def cancel_order(order_id):
    order = Order.query.get_or_404(order_id)
    order.status = 'cancelled'
    db.session.commit()
    socketio.emit('new_order_event', {'message': 'order_updated'})
    return redirect(url_for('merchant_index'))

if __name__ == '__main__':
    if not os.path.exists('static'):
        os.makedirs('static')
    qr_path = os.path.join('static', 'qrcode.png')
    qr_img = qrcode.make('https://scrabble-flatten-exerciser.ngrok-free.dev/')
    qr_img.save(qr_path)
    
    socketio.run(app, debug=True)