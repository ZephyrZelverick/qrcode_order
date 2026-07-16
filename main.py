from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import json
from database import get_db_connection

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class CartItem(BaseModel):
    menu_item: str
    quantity: int
    special_instructions: Optional[str] = ""

class OrderCreateSchema(BaseModel):
    table_number: str
    total_price: float
    items: List[CartItem]

class ConnectionManager:
    def __init__(self):
        self.merchant_connections = []
        self.customer_connections = {}

    async def connect_merchant(self, websocket):
        await websocket.accept()
        self.merchant_connections.append(websocket)

    def disconnect_merchant(self, websocket):
        self.merchant_connections.remove(websocket)

    async def send_to_merchants(self, message):
        for conn in self.merchant_connections:
            await conn.send_text(message)

manager = ConnectionManager()

@app.post("/api/order")
async def create_order(order: OrderCreateSchema):
    conn = get_db_connection()
    cursor = conn.cursor()
    # บันทึกคิวหลัก
    cursor.execute("INSERT INTO orders (table_number, total_price, status) VALUES (%s, %s, 'pending')", 
                   (order.table_number, order.total_price))
    order_id = cursor.lastrowid
    # บันทึกรายการอาหาร
    for item in order.items:
        cursor.execute("INSERT INTO order_items (order_id, menu_item, quantity, special_instructions) VALUES (%s, %s, %s, %s)",
                       (order_id, item.menu_item, item.quantity, item.special_instructions))
    conn.commit()
    conn.close()
    await manager.send_to_merchants(json.dumps({"event": "new_order"}))
    return {"status": "success", "order_ids": [order_id]}

@app.get("/api/orders")
def get_orders():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = """SELECT o.id, o.table_number, o.total_price, o.status, 
               GROUP_CONCAT(CONCAT(oi.menu_item, ' x', oi.quantity) SEPARATOR ', ') as items_list,
               GROUP_CONCAT(oi.special_instructions SEPARATOR ', ') as notes
               FROM orders o LEFT JOIN order_items oi ON o.id = oi.order_id 
               WHERE o.status IN ('pending', 'cooking') GROUP BY o.id"""
    cursor.execute(query)
    res = cursor.fetchall()
    conn.close()
    return res

@app.websocket("/ws/merchant")
async def websocket_merchant(websocket: WebSocket):
    await manager.connect_merchant(websocket)
    try:
        while True: await websocket.receive_text()
    except: manager.disconnect_merchant(websocket)