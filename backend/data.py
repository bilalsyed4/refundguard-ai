import sqlite3
from pathlib import Path
from datetime import date, timedelta
from typing import Dict, Any, Optional, List

DB_PATH = Path(__file__).resolve().parent / "crm.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create and seed the mock CRM database."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            tier TEXT NOT NULL,
            suspicious_refund_history INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            amount REAL NOT NULL,
            purchase_date TEXT NOT NULL,
            delivered_date TEXT NOT NULL,
            final_sale INTEGER NOT NULL DEFAULT 0,
            product_type TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
        )
        """
    )

    customer_count = cur.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
    if customer_count == 0:
        seed_data(cur)

    conn.commit()
    conn.close()


def seed_data(cur: sqlite3.Cursor) -> None:
    today = date.today()

    customers = [
        ("CUST-001", "Aisha Khan", "aisha@example.com", "gold", 0),
        ("CUST-002", "Mike Johnson", "mike@example.com", "standard", 0),
        ("CUST-003", "Sarah Lee", "sarah@example.com", "standard", 0),
        ("CUST-004", "David Miller", "david@example.com", "platinum", 0),
        ("CUST-005", "Priya Patel", "priya@example.com", "standard", 1),
        ("CUST-006", "Omar Ahmed", "omar@example.com", "gold", 0),
        ("CUST-007", "Emily Chen", "emily@example.com", "standard", 0),
        ("CUST-008", "Carlos Rivera", "carlos@example.com", "standard", 0),
        ("CUST-009", "Nina Brown", "nina@example.com", "gold", 0),
        ("CUST-010", "Daniel Wilson", "daniel@example.com", "standard", 0),
        ("CUST-011", "Fatima Ali", "fatima@example.com", "platinum", 0),
        ("CUST-012", "Tom Baker", "tom@example.com", "standard", 0),
        ("CUST-013", "Grace Kim", "grace@example.com", "standard", 1),
        ("CUST-014", "James Carter", "james@example.com", "gold", 0),
        ("CUST-015", "Lina Garcia", "lina@example.com", "standard", 0),
    ]

    orders = [
        # Approved: regular item, within 30 days, below $500
        ("ORD-1001", "CUST-001", "Running Shoes", 120.00, today - timedelta(days=10), today - timedelta(days=8), 0, "regular", "delivered"),
        # Denied: final sale
        ("ORD-1002", "CUST-002", "Clearance Jacket", 89.00, today - timedelta(days=12), today - timedelta(days=10), 1, "regular", "delivered"),
        # Denied: too old
        ("ORD-1003", "CUST-003", "Backpack", 140.00, today - timedelta(days=45), today - timedelta(days=42), 0, "regular", "delivered"),
        # Escalated: over $500
        ("ORD-1004", "CUST-004", "Gaming Monitor", 780.00, today - timedelta(days=5), today - timedelta(days=3), 0, "regular", "delivered"),
        # Escalated: suspicious customer
        ("ORD-1005", "CUST-005", "Bluetooth Speaker", 220.00, today - timedelta(days=6), today - timedelta(days=5), 0, "regular", "delivered"),
        # Denied: gift card
        ("ORD-1006", "CUST-006", "Digital Gift Card", 100.00, today - timedelta(days=3), today - timedelta(days=3), 0, "gift_card", "delivered"),
        ("ORD-1007", "CUST-007", "Yoga Mat", 55.00, today - timedelta(days=2), today - timedelta(days=1), 0, "regular", "delivered"),
        ("ORD-1008", "CUST-008", "Wireless Mouse", 35.00, today - timedelta(days=25), today - timedelta(days=23), 0, "regular", "delivered"),
        ("ORD-1009", "CUST-009", "Winter Boots", 210.00, today - timedelta(days=33), today - timedelta(days=30), 0, "regular", "delivered"),
        ("ORD-1010", "CUST-010", "Final Sale Watch", 310.00, today - timedelta(days=7), today - timedelta(days=6), 1, "regular", "delivered"),
        ("ORD-1011", "CUST-011", "Laptop Dock", 160.00, today - timedelta(days=14), today - timedelta(days=12), 0, "regular", "delivered"),
        ("ORD-1012", "CUST-012", "Premium Headphones", 520.00, today - timedelta(days=9), today - timedelta(days=8), 0, "regular", "delivered"),
        ("ORD-1013", "CUST-013", "Coffee Maker", 130.00, today - timedelta(days=4), today - timedelta(days=2), 0, "regular", "delivered"),
        ("ORD-1014", "CUST-014", "Desk Chair", 440.00, today - timedelta(days=20), today - timedelta(days=18), 0, "regular", "delivered"),
        ("ORD-1015", "CUST-015", "Phone Case", 25.00, today - timedelta(days=1), today - timedelta(days=1), 0, "regular", "delivered"),
    ]

    cur.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", customers)
    cur.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(o[0], o[1], o[2], o[3], o[4].isoformat(), o[5].isoformat(), o[6], o[7], o[8]) for o in orders],
    )


def lookup_order(order_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT orders.*, customers.name, customers.email, customers.tier, customers.suspicious_refund_history
        FROM orders
        JOIN customers ON orders.customer_id = customers.customer_id
        WHERE orders.order_id = ?
        """,
        (order_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_orders() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT orders.order_id, customers.name, orders.product_name, orders.amount,
               orders.purchase_date, orders.final_sale, orders.product_type
        FROM orders
        JOIN customers ON orders.customer_id = customers.customer_id
        ORDER BY orders.order_id
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
