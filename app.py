import streamlit as st
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

DB = Path(__file__).with_name("nesting.db")

def conn():
    return sqlite3.connect(DB)

def init_db():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS plates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_id TEXT UNIQUE,
            material TEXT,
            thickness REAL,
            width REAL,
            length REAL,
            source TEXT DEFAULT 'STOCK',
            parent_plate TEXT,
            status TEXT DEFAULT 'AVAILABLE',
            created_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            project TEXT,
            material TEXT,
            thickness REAL,
            plate_id TEXT,
            utilization REAL,
            created_at TEXT
        )""")
        n = c.execute("SELECT COUNT(*) FROM plates").fetchone()[0]
        if n == 0:
            seed = [
                ("S516N-10-001","SA-516 Gr.70N",10,2000,6000),
                ("S516N-10-002","SA-516 Gr.70N",10,2500,12000),
                ("S516N-12-001","SA-516 Gr.70N",12,2000,6000),
                ("S516N-12-002","SA-516 Gr.70N",12,2500,12000),
                ("S516N-16-001","SA-516 Gr.70N",16,2500,12000),
                ("S516N-20-001","SA-516 Gr.70N",20,2500,12000),
                ("SA36-6-001","SA-36",6,1500,6000),
                ("SA36-8-001","SA-36",8,2000,6000),
                ("SA36-10-001","SA-36",10,2000,6000),
                ("SA36-10-002","SA-36",10,2500,12000),
                ("SA36-12-001","SA-36",12,2000,6000),
                ("SA36-16-001","SA-36",16,2500,12000),
            ]
            now = datetime.now().isoformat(timespec="seconds")
            c.executemany("""INSERT INTO plates
                (plate_id,material,thickness,width,length,source,parent_plate,status,created_at)
                VALUES(?,?,?,?,?,'STOCK',NULL,'AVAILABLE',?)""",
                [(*x, now) for x in seed])

def get_inventory():
    with conn() as c:
        return pd.read_sql_query("SELECT * FROM plates ORDER BY source DESC, material, thickness, plate_id", c)

def available(material, thickness):
    with conn() as c:
        return pd.read_sql_query("""SELECT * FROM plates
            WHERE status='AVAILABLE' AND material=? AND ABS(thickness-?)<0.001
            ORDER BY CASE WHEN source='REMNANT' THEN 0 ELSE 1 END, width*length ASC""",
            c, params=(material, thickness))

def pack_shelves(W, L, parts, kerf, margin):
    # Simple V1 shelf nesting. Parts are placed left-to-right, then a new row.
    usable_w, usable_l = W - 2*margin, L - 2*margin
    x, y, row_h = margin, margin, 0
    placed, missing = [], []
    expanded = []
    for p in parts:
        for i in range(int(p["qty"])):
            expanded.append({"part": p["part"], "w": float(p["width"]), "l": float(p["length"]),
                             "rotate": bool(p["rotate"]), "copy": i+1})
    expanded.sort(key=lambda p: max(p["w"],p["l"]), reverse=True)

    for p in expanded:
        options = [(p["w"],p["l"],False)]
        if p["rotate"] and p["w"] != p["l"]:
            options.append((p["l"],p["w"],True))
        done = False
        for new_row in (False, True):
            tx = margin if new_row else x
            ty = y + row_h + (kerf if row_h else 0) if new_row else y
            for w,l,rot in options:
                if tx + w <= margin + usable_w + 1e-9 and ty + l <= margin + usable_l + 1e-9:
                    if new_row:
                        x, y, row_h = margin, ty, 0
                    placed.append({**p,"x":x,"y":y,"pw":w,"pl":l,"rotated":rot})
                    x += w + kerf
                    row_h = max(row_h,l)
                    done = True
                    break
            if done: break
        if not done:
            missing.append(p)
    return placed, missing

def choose_plate(material, thickness, parts, kerf, margin):
    inv = available(material, thickness)
    best = None
    for _, r in inv.iterrows():
        placed, missing = pack_shelves(r.width, r.length, parts, kerf, margin)
        if not missing:
            used = sum(p["pw"]*p["pl"] for p in placed)
            util = used/(r.width*r.length)*100
            candidate = (0 if r.source=="REMNANT" else 1, r.width*r.length, -util, r, placed)
            if best is None or candidate[:3] < best[:3]:
                best = candidate
    return None if best is None else (best[3], best[4])

def draw_nest(plate, placed, margin):
    fig, ax = plt.subplots(figsize=(11,5))
    ax.add_patch(Rectangle((0,0), plate.width, plate.length, fill=False, linewidth=2))
    if margin:
        ax.add_patch(Rectangle((margin,margin), plate.width-2*margin, plate.length-2*margin,
                               fill=False, linestyle="--", linewidth=1))
    for p in placed:
        ax.add_patch(Rectangle((p["x"],p["y"]),p["pw"],p["pl"],alpha=.25))
        ax.text(p["x"]+p["pw"]/2,p["y"]+p["pl"]/2,
                f'{p["part"]}-{p["copy"]}\n{int(p["pw"])}×{int(p["pl"])}',
                ha="center",va="center",fontsize=8)
    ax.set_xlim(0,plate.width); ax.set_ylim(0,plate.length)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Width (mm)"); ax.set_ylabel("Length (mm)")
    ax.set_title(f'{plate.plate_id} | {plate.material} | {plate.thickness:g} mm')
    return fig

def save_run(project, plate, placed, min_remnant):
    used = sum(p["pw"]*p["pl"] for p in placed)
    util = used/(plate.width*plate.length)*100
    run_id = "RUN-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    now = datetime.now().isoformat(timespec="seconds")

    # V1 remnant model: saves the largest simple unused rectangle above the used rows.
    max_y = max((p["y"]+p["pl"] for p in placed), default=0)
    rem_l = plate.length - max_y
    rem_w = plate.width
    with conn() as c:
        c.execute("UPDATE plates SET status='USED' WHERE id=?", (int(plate.id),))
        c.execute("""INSERT INTO runs(run_id,project,material,thickness,plate_id,utilization,created_at)
                     VALUES(?,?,?,?,?,?,?)""",
                  (run_id,project,plate.material,float(plate.thickness),plate.plate_id,util,now))
        if rem_l >= min_remnant and rem_w >= min_remnant:
            rid = "REM-" + datetime.now().strftime("%Y%m%d%H%M%S")
            c.execute("""INSERT INTO plates
                (plate_id,material,thickness,width,length,source,parent_plate,status,created_at)
                VALUES(?,?,?,?,?,'REMNANT',?,'AVAILABLE',?)""",
                (rid,plate.material,float(plate.thickness),rem_w,rem_l,plate.plate_id,now))
    return run_id, util, (rem_w, rem_l) if rem_l >= min_remnant and rem_w >= min_remnant else None

st.set_page_config(page_title="Plate Nesting V1", layout="wide")
init_db()
st.title("Plate Nesting & Remnant Manager — V1")
st.caption("Prototype: rectangular parts, 90° rotation, stock/remnant selection and SQLite inventory.")

tab1, tab2, tab3 = st.tabs(["Create Nest", "Plate / Remnant Database", "Test Case"])

with tab1:
    c1,c2,c3,c4 = st.columns(4)
    project = c1.text_input("Project / Job Order","TEST-001")
    material = c2.selectbox("Material",["SA-516 Gr.70N","SA-36"])
    thickness = c3.number_input("Thickness (mm)",min_value=1.0,value=10.0,step=1.0)
    kerf = c4.number_input("Kerf / spacing (mm)",min_value=0.0,value=3.0,step=0.5)
    margin = st.number_input("Plate edge margin (mm)",min_value=0.0,value=10.0,step=1.0)

    default = pd.DataFrame([
        {"part":"P01","width":1200.0,"length":800.0,"qty":2,"rotate":True},
        {"part":"P02","width":1800.0,"length":600.0,"qty":2,"rotate":True},
        {"part":"P03","width":500.0,"length":400.0,"qty":4,"rotate":True},
    ])
    parts_df = st.data_editor(default, num_rows="dynamic", use_container_width=True,
        column_config={"rotate":st.column_config.CheckboxColumn("Rotate 90°")})

    if st.button("Generate Nest", type="primary"):
        parts = parts_df.dropna(subset=["part","width","length","qty"]).to_dict("records")
        result = choose_plate(material, thickness, parts, kerf, margin)
        if result is None:
            st.error("No single available plate/remnant can fit all requested parts in this V1 algorithm.")
        else:
            plate, placed = result
            st.session_state["result"] = (plate.to_dict(), placed, project, margin)
            st.success(f"Selected {plate.source}: {plate.plate_id} — {plate.width:g} × {plate.length:g} × {plate.thickness:g} mm")
            used = sum(p["pw"]*p["pl"] for p in placed)
            st.metric("Part area utilization",f"{used/(plate.width*plate.length)*100:.1f}%")
            st.pyplot(draw_nest(plate,placed,margin))

    if "result" in st.session_state:
        plate_d, placed, saved_project, saved_margin = st.session_state["result"]
        plate = pd.Series(plate_d)
        if st.button("Approve Nest & Update Inventory"):
            run_id, util, rem = save_run(saved_project, plate, placed, min_remnant=300)
            st.success(f"{run_id} saved. Original plate marked USED.")
            if rem:
                st.info(f"Reusable V1 remnant saved automatically: {rem[0]:g} × {rem[1]:g} mm.")
            else:
                st.info("No reusable rectangular remnant above the 300 mm minimum was created.")
            del st.session_state["result"]
            st.rerun()

with tab2:
    inv = get_inventory()
    st.dataframe(inv[["plate_id","material","thickness","width","length","source","parent_plate","status"]],
                 use_container_width=True, hide_index=True)
    st.subheader("Add temporary stock plate")
    a,b,c,d,e = st.columns(5)
    pid=a.text_input("Plate ID","NEW-001")
    mat=b.selectbox("Material ",["SA-516 Gr.70N","SA-36"])
    thk=c.number_input("Thickness ",1.0,100.0,10.0)
    wid=d.number_input("Width ",100.0,5000.0,2000.0)
    leng=e.number_input("Length ",100.0,20000.0,6000.0)
    if st.button("Add Plate"):
        try:
            with conn() as cx:
                cx.execute("""INSERT INTO plates
                (plate_id,material,thickness,width,length,source,status,created_at)
                VALUES(?,?,?,?,?,'STOCK','AVAILABLE',?)""",
                (pid,mat,thk,wid,leng,datetime.now().isoformat(timespec="seconds")))
            st.success("Plate added.")
            st.rerun()
        except sqlite3.IntegrityError:
            st.error("Plate ID already exists.")

with tab3:
    st.markdown("""
### Test 1
Use the defaults already loaded:

- Material: **SA-516 Gr.70N**
- Thickness: **10 mm**
- Kerf: **3 mm**
- Edge margin: **10 mm**
- P01: 1200 × 800, Qty 2
- P02: 1800 × 600, Qty 2
- P03: 500 × 400, Qty 4

Click **Generate Nest**. The application should select an available 10 mm SA-516 Gr.70N plate and show the layout.

Then click **Approve Nest & Update Inventory**. The selected plate becomes `USED`; if the simple V1 leftover rectangle is at least 300 × 300 mm, it is saved as a `REMNANT`.

### Test 2 — verify automatic remnant reuse
Create a smaller second job using the same material/thickness. If the saved remnant can contain all its parts, the selection logic gives the remnant priority over a new stock plate.

> V1 limitation: this prototype saves one simple rectangular remnant and requires the complete job to fit on one plate. Multi-plate nesting and irregular polygon remnants are the next upgrade.
""")
