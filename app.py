import streamlit as st
import sqlite3, io, json
from pathlib import Path
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import ezdxf

DB = Path(__file__).with_name("nesting.db")

# ---------------- DATABASE ----------------
def db():
    return sqlite3.connect(DB)

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS plates(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_id TEXT UNIQUE, material TEXT, thickness REAL,
            width REAL, length REAL, source TEXT DEFAULT 'STOCK',
            parent_plate TEXT, status TEXT DEFAULT 'AVAILABLE',
            heat_no TEXT DEFAULT '', location TEXT DEFAULT '',
            created_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE, project TEXT, material TEXT, thickness REAL,
            plate_id TEXT, plate_width REAL, plate_length REAL,
            utilization REAL, layout_json TEXT, created_at TEXT
        )""")
        if c.execute("SELECT COUNT(*) FROM plates").fetchone()[0] == 0:
            seed = [
                ("S516N-10-001","SA-516 Gr.70N",10,2000,6000,"H-T001","Rack A1"),
                ("S516N-10-002","SA-516 Gr.70N",10,2500,12000,"H-T002","Rack A1"),
                ("S516N-12-001","SA-516 Gr.70N",12,2000,6000,"H-T003","Rack A2"),
                ("S516N-12-002","SA-516 Gr.70N",12,2500,12000,"H-T004","Rack A2"),
                ("S516N-16-001","SA-516 Gr.70N",16,2500,12000,"H-T005","Rack A3"),
                ("S516N-20-001","SA-516 Gr.70N",20,2500,12000,"H-T006","Rack A3"),
                ("SA36-6-001","SA-36",6,1500,6000,"H-A001","Rack B1"),
                ("SA36-8-001","SA-36",8,2000,6000,"H-A002","Rack B1"),
                ("SA36-10-001","SA-36",10,2000,6000,"H-A003","Rack B2"),
                ("SA36-10-002","SA-36",10,2500,12000,"H-A004","Rack B2"),
                ("SA36-12-001","SA-36",12,2000,6000,"H-A005","Rack B3"),
                ("SA36-16-001","SA-36",16,2500,12000,"H-A006","Rack B3"),
            ]
            now = datetime.now().isoformat(timespec="seconds")
            c.executemany("""INSERT INTO plates
            (plate_id,material,thickness,width,length,source,parent_plate,status,heat_no,location,created_at)
            VALUES(?,?,?,?,?,'STOCK',NULL,'AVAILABLE',?,?,?)""",
            [(*r,now) for r in seed])

def inventory():
    with db() as c:
        return pd.read_sql_query("SELECT * FROM plates ORDER BY status, source DESC, material, thickness, plate_id",c)

def runs():
    with db() as c:
        return pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC",c)

def available(material,thickness):
    with db() as c:
        return pd.read_sql_query("""SELECT * FROM plates
        WHERE status='AVAILABLE' AND material=? AND ABS(thickness-?)<0.001
        ORDER BY CASE WHEN source='REMNANT' THEN 0 ELSE 1 END, width*length ASC""",
        c,params=(material,thickness))

# ---------------- NESTING ----------------
def expand_parts(df):
    items=[]
    for _,r in df.dropna(subset=["part","width","length","qty"]).iterrows():
        for n in range(int(r.qty)):
            items.append(dict(part=str(r.part),copy=n+1,w=float(r.width),l=float(r.length),
                              rotate=bool(r.rotate)))
    return sorted(items,key=lambda p:max(p["w"],p["l"]),reverse=True)

def pack(W,L,df,kerf,margin):
    items=expand_parts(df)
    x=y=margin; row_h=0; placed=[]; missing=[]
    maxx=W-margin; maxy=L-margin
    for p in items:
        opts=[(p["w"],p["l"],False)]
        if p["rotate"] and p["w"] != p["l"]: opts.append((p["l"],p["w"],True))
        done=False
        # current row
        for w,l,rot in opts:
            if x+w<=maxx and y+l<=maxy:
                placed.append({**p,"x":x,"y":y,"pw":w,"pl":l,"rotated":rot})
                x+=w+kerf; row_h=max(row_h,l); done=True; break
        # next row
        if not done:
            nx=margin; ny=y+row_h+kerf
            for w,l,rot in opts:
                if nx+w<=maxx and ny+l<=maxy:
                    x=nx; y=ny; row_h=0
                    placed.append({**p,"x":x,"y":y,"pw":w,"pl":l,"rotated":rot})
                    x+=w+kerf; row_h=max(row_h,l); done=True; break
        if not done: missing.append(p)
    return placed,missing

def select_plate(material,thickness,df,kerf,margin):
    candidates=[]
    for _,p in available(material,thickness).iterrows():
        placed,missing=pack(p.width,p.length,df,kerf,margin)
        if not missing:
            used=sum(x["pw"]*x["pl"] for x in placed)
            candidates.append((0 if p.source=="REMNANT" else 1,p.width*p.length,-used/(p.width*p.length),p,placed))
    if not candidates:return None
    candidates.sort(key=lambda x:x[:3])
    return candidates[0][3],candidates[0][4]

# ---------------- DRAWING / EXPORT ----------------
def layout_figure(plate,placed,margin=0,title=None):
    fig,ax=plt.subplots(figsize=(12,6))
    W=float(plate["width"] if isinstance(plate,dict) else plate.width)
    L=float(plate["length"] if isinstance(plate,dict) else plate.length)
    pid=plate["plate_id"] if isinstance(plate,dict) else plate.plate_id
    ax.add_patch(Rectangle((0,0),W,L,facecolor="#b7e4c7",edgecolor="black",linewidth=2))
    if margin:
        ax.add_patch(Rectangle((margin,margin),W-2*margin,L-2*margin,fill=False,
                               edgecolor="black",linestyle="--",linewidth=1))
    for p in placed:
        ax.add_patch(Rectangle((p["x"],p["y"]),p["pw"],p["pl"],
                               facecolor="#e63946",edgecolor="black",linewidth=1))
        ax.text(p["x"]+p["pw"]/2,p["y"]+p["pl"]/2,
                f'{p["part"]}-{p["copy"]}\n{p["pw"]:g}×{p["pl"]:g}',
                ha="center",va="center",fontsize=8,color="white",weight="bold")
    ax.set_xlim(0,W);ax.set_ylim(0,L);ax.set_aspect("equal",adjustable="box")
    ax.set_xlabel("Width (mm)");ax.set_ylabel("Length (mm)")
    ax.set_title(title or f"Plate {pid} — RED = used / GREEN = remaining")
    ax.grid(False);fig.tight_layout()
    return fig

def dxf_bytes(plate,placed,margin=0):
    doc=ezdxf.new("R2010")
    doc.layers.add("PLATE",color=3)   # green
    doc.layers.add("USED_PARTS",color=1) # red
    doc.layers.add("TEXT",color=7)
    msp=doc.modelspace()
    W=float(plate["width"] if isinstance(plate,dict) else plate.width)
    L=float(plate["length"] if isinstance(plate,dict) else plate.length)
    msp.add_lwpolyline([(0,0),(W,0),(W,L),(0,L),(0,0)],dxfattribs={"layer":"PLATE"})
    for p in placed:
        x,y,w,l=p["x"],p["y"],p["pw"],p["pl"]
        msp.add_lwpolyline([(x,y),(x+w,y),(x+w,y+l),(x,y+l),(x,y)],dxfattribs={"layer":"USED_PARTS"})
        txt=msp.add_text(f'{p["part"]}-{p["copy"]}  {w:g}x{l:g}',height=max(20,min(w,l)*0.08),
                         dxfattribs={"layer":"TEXT"})
        txt.dxf.insert=(x+w*0.05,y+l*0.5)
    s=io.StringIO();doc.write(s)
    return s.getvalue().encode("utf-8")

def csv_report(plate,placed,project):
    rows=[]
    for p in placed:
        rows.append({"Project":project,"Plate ID":plate["plate_id"],"Part":p["part"],
                     "Copy":p["copy"],"Width":p["pw"],"Length":p["pl"],
                     "X":p["x"],"Y":p["y"],"Rotated":p["rotated"]})
    return pd.DataFrame(rows).to_csv(index=False).encode()

def approve(project,plate,placed,min_remnant=300):
    now=datetime.now().isoformat(timespec="seconds")
    run_id="RUN-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    used=sum(p["pw"]*p["pl"] for p in placed)
    util=used/(plate["width"]*plate["length"])*100
    max_y=max((p["y"]+p["pl"] for p in placed),default=0)
    rem_w=float(plate["width"]); rem_l=float(plate["length"])-max_y
    with db() as c:
        c.execute("UPDATE plates SET status='USED' WHERE id=?",(int(plate["id"]),))
        c.execute("""INSERT INTO runs(run_id,project,material,thickness,plate_id,plate_width,plate_length,
        utilization,layout_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (run_id,project,plate["material"],plate["thickness"],plate["plate_id"],plate["width"],plate["length"],
         util,json.dumps(placed),now))
        if rem_w>=min_remnant and rem_l>=min_remnant:
            rid="REM-"+datetime.now().strftime("%Y%m%d%H%M%S%f")
            c.execute("""INSERT INTO plates
            (plate_id,material,thickness,width,length,source,parent_plate,status,heat_no,location,created_at)
            VALUES(?,?,?,?,?,'REMNANT',?,'AVAILABLE',?,?,?)""",
            (rid,plate["material"],plate["thickness"],rem_w,rem_l,plate["plate_id"],
             plate.get("heat_no",""),plate.get("location",""),now))
    return run_id,util,(rem_w,rem_l) if rem_w>=min_remnant and rem_l>=min_remnant else None

# ---------------- UI ----------------
st.set_page_config(page_title="Plate Nesting V2",page_icon="✂️",layout="wide")
init_db()
st.title("✂️ Plate Nesting & Remnant Management — V2")
st.caption("Prototype for rectangular plate nesting. RED = consumed area, GREEN = remaining plate.")

t1,t2,t3,t4=st.tabs(["New Nest","Plate Database","Used Plate Viewer","Run History"])

with t1:
    a,b,c,d,e=st.columns(5)
    project=a.text_input("Project / Job Order","TEST-001")
    material=b.selectbox("Material",["SA-516 Gr.70N","SA-36"])
    thickness=c.number_input("Thickness (mm)",1.0,100.0,10.0,1.0)
    kerf=d.number_input("Kerf / part spacing (mm)",0.0,50.0,3.0,0.5)
    margin=e.number_input("Edge margin (mm)",0.0,200.0,10.0,1.0)

    test=pd.DataFrame([
        {"part":"P01","width":1200.0,"length":800.0,"qty":2,"rotate":True},
        {"part":"P02","width":1800.0,"length":600.0,"qty":2,"rotate":True},
        {"part":"P03","width":500.0,"length":400.0,"qty":4,"rotate":True},
    ])
    parts=st.data_editor(test,num_rows="dynamic",use_container_width=True,
        column_config={"rotate":st.column_config.CheckboxColumn("Allow 90° Rotation")})

    if st.button("Generate Cutting Plan",type="primary"):
        result=select_plate(material,thickness,parts,kerf,margin)
        if result is None:
            st.error("No single available plate/remnant can contain this complete job. Multi-plate nesting is planned for V3.")
        else:
            p,placed=result
            plate=p.to_dict()
            st.session_state["nest"]={"plate":plate,"placed":placed,"project":project,"margin":margin}
    if "nest" in st.session_state:
        n=st.session_state["nest"];plate=n["plate"];placed=n["placed"]
        used=sum(p["pw"]*p["pl"] for p in placed);area=plate["width"]*plate["length"]
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Selected",plate["plate_id"])
        c2.metric("Plate",f'{plate["width"]:g} × {plate["length"]:g} mm')
        c3.metric("Used area",f"{used/area*100:.1f}%")
        c4.metric("Remaining area",f"{(area-used)/area*100:.1f}%")
        st.pyplot(layout_figure(plate,placed,n["margin"]))
        x,y=st.columns(2)
        x.download_button("Download DXF Cutting Layout",dxf_bytes(plate,placed,n["margin"]),
                          file_name=f'{n["project"]}_{plate["plate_id"]}.dxf',mime="application/dxf")
        y.download_button("Download Cutting List CSV",csv_report(plate,placed,n["project"]),
                          file_name=f'{n["project"]}_cutting_list.csv',mime="text/csv")
        st.info("DXF opens in AutoCAD and can be Save As DWG. Direct DWG writing is not included because DWG is a proprietary binary format.")
        if st.button("Approve Nest & Update Inventory"):
            run_id,util,rem=approve(n["project"],plate,placed)
            st.success(f"{run_id} approved. {plate['plate_id']} is now shown as USED.")
            if rem:st.success(f"Reusable rectangular remnant saved: {rem[0]:g} × {rem[1]:g} mm.")
            else:st.warning("No rectangular remnant ≥ 300 mm was saved.")
            del st.session_state["nest"]
            st.rerun()

with t2:
    inv=inventory()
    f1,f2=st.columns(2)
    status=f1.multiselect("Status filter",["AVAILABLE","USED"],default=["AVAILABLE","USED"])
    source=f2.multiselect("Source filter",["STOCK","REMNANT"],default=["STOCK","REMNANT"])
    view=inv[inv.status.isin(status)&inv.source.isin(source)]
    st.dataframe(view[["plate_id","material","thickness","width","length","source","parent_plate","status","heat_no","location"]],
                 use_container_width=True,hide_index=True)
    st.subheader("Add Stock Plate")
    cols=st.columns(7)
    pid=cols[0].text_input("Plate ID","NEW-001")
    mat=cols[1].selectbox("Material ",["SA-516 Gr.70N","SA-36"])
    thk=cols[2].number_input("Thk",1.0,100.0,10.0)
    wid=cols[3].number_input("Width",100.0,5000.0,2000.0)
    leng=cols[4].number_input("Length",100.0,20000.0,6000.0)
    heat=cols[5].text_input("Heat No.","")
    loc=cols[6].text_input("Location","Rack A")
    if st.button("Add to Inventory"):
        try:
            with db() as c:
                c.execute("""INSERT INTO plates(plate_id,material,thickness,width,length,source,status,heat_no,location,created_at)
                VALUES(?,?,?,?,?,'STOCK','AVAILABLE',?,?,?)""",
                (pid,mat,thk,wid,leng,heat,loc,datetime.now().isoformat(timespec="seconds")))
            st.success("Plate added.");st.rerun()
        except sqlite3.IntegrityError:st.error("Plate ID already exists.")

with t3:
    rh=runs()
    if rh.empty:
        st.info("No approved cutting runs yet. Approve a nest first.")
    else:
        labels=[f'{r.run_id} | {r.plate_id} | {r.project}' for _,r in rh.iterrows()]
        choice=st.selectbox("Select used plate / cutting run",labels)
        r=rh.iloc[labels.index(choice)]
        plate={"plate_id":r.plate_id,"width":r.plate_width,"length":r.plate_length}
        placed=json.loads(r.layout_json)
        st.pyplot(layout_figure(plate,placed,0,f"{r.run_id} — {r.project} | RED used / GREEN balance"))
        a,b,c=st.columns(3)
        a.metric("Material",r.material);b.metric("Thickness",f"{r.thickness:g} mm");c.metric("Utilization",f"{r.utilization:.1f}%")
        st.download_button("Download Historical DXF",dxf_bytes(plate,placed),
                           file_name=f'{r.run_id}_{r.plate_id}.dxf',mime="application/dxf")

with t4:
    rh=runs()
    if rh.empty:st.info("No runs recorded.")
    else:
        st.dataframe(rh[["run_id","project","material","thickness","plate_id","utilization","created_at"]],
                     use_container_width=True,hide_index=True)
        st.download_button("Download Run History CSV",rh.to_csv(index=False).encode(),
                           file_name="nesting_run_history.csv",mime="text/csv")

st.divider()
st.caption("Engineering prototype V2. Verify layouts before fabrication. V2 rectangular remnant logic does not yet represent irregular leftover polygons.")
