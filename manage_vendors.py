"""CLI to add/list/remove vendors from your database.

Usage:
  python manage_vendors.py add
  python manage_vendors.py list
  python manage_vendors.py remove "Vendor Name"
"""
import sys
from dotenv import load_dotenv
load_dotenv()
import db

db.init_db()


def add():
    print("Add a vendor to your database")
    print("─" * 40)
    name     = input("Vendor name:               ").strip()
    phone    = input("Phone number (+1xxxxxxxxxx): ").strip()
    website  = input("Website URL:               ").strip()
    supplies = input("What do they supply?       ").strip()
    min_ord  = input("Min order quantity [0]:    ").strip() or "0"
    max_ord  = input("Max order quantity [999999]: ").strip() or "999999"
    notes    = input("Notes (optional):          ").strip()

    db.add_vendor(
        name=name, phone=phone, website=website, supplies=supplies,
        min_order=int(min_ord), max_order=int(max_ord), notes=notes,
    )
    print(f"\n✅ Added: {name}")


def list_all():
    vendors = db.list_vendors()
    if not vendors:
        print("No vendors in database.")
        return
    print(f"\n{'Name':<30} {'Phone':<18} {'Supplies':<30} {'Orders':<15} {'Source'}")
    print("─" * 100)
    for v in vendors:
        orders = f"{v['min_order']}–{v['max_order']}"
        print(f"{v['name']:<30} {v['phone'] or '—':<18} {v['supplies'] or '—':<30} {orders:<15} {v['notes'] or ''}")


def remove(name: str):
    with db.get_conn() as conn:
        conn.execute("UPDATE vendors SET active=0 WHERE LOWER(name)=LOWER(?)", (name,))
    print(f"Removed: {name}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "add":
        add()
    elif cmd == "list":
        list_all()
    elif cmd == "remove" and len(sys.argv) > 2:
        remove(sys.argv[2])
    else:
        print(__doc__)
