"""
主厨到家 · 上门私厨预约平台 —— 后端服务
技术栈：FastAPI + SQLite（标准库 sqlite3）+ HMAC 签名 Token
启动：uvicorn server.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import re
import sqlite3
import string
import time
from datetime import datetime, timedelta, date, timezone
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 配置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path: str) -> None:
    """极简 .env 加载：已存在的环境变量优先，不覆盖。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env(os.path.join(BASE_DIR, ".env"))

DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "app.db"))
WEB_DIR = os.path.join(BASE_DIR, "web")
SECRET = os.environ.get("APP_SECRET", "zhuchu-daojia-dev-secret-change-me")
DEV_MODE = os.environ.get("DEV_MODE", "1") == "1"      # 开发模式：验证码直接返回
TOKEN_TTL = 7 * 24 * 3600                              # 7 天
PLATFORM_FEE = 9
TZ = timezone(timedelta(hours=8))

STATUS_TEXT = {
    "pending": "等待厨师上门",
    "serving": "厨师服务中",
    "to_review": "待评价",
    "done": "已完成",
    "canceled": "已取消",
}

# ---------------------------------------------------------------- 建库 & 种子数据
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE NOT NULL,
  nickname TEXT, avatar TEXT, level INT DEFAULT 1, points INT DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chefs(
  id INTEGER PRIMARY KEY, name TEXT, avatar TEXT, cuisine TEXT,
  rating REAL, order_count INT, distance REAL, base_price INT,
  tags TEXT, badges TEXT, categories TEXT, bio TEXT, hero TEXT);
CREATE TABLE IF NOT EXISTS packages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chef_id INT, name TEXT, desc TEXT,
  price INT, food_cost INT, minutes INT, sort INT DEFAULT 0, cover TEXT);
CREATE TABLE IF NOT EXISTS orders(
  id TEXT PRIMARY KEY, user_id INT, chef_id INT, package_id INT,
  service_date TEXT, slot TEXT, people INT, tastes TEXT, address TEXT,
  contact TEXT, note TEXT, service_fee INT, food_fee INT, platform_fee INT,
  total INT, status TEXT, created_at TEXT, demo INT DEFAULT 0,
  reviewed INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS order_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, title TEXT,
  detail TEXT, at TEXT, done INT DEFAULT 0, sort INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, user_id INT,
  chef_id INT, rating REAL, tags TEXT, content TEXT, anonymous INT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT, category TEXT,
  title TEXT, body TEXT, read INT DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS codes(
  phone TEXT PRIMARY KEY, code TEXT, expires_at REAL);
"""

IMG = {
    "hero": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/Ko4AbmE5BoL7FAx1sq2cl1NInvf",
    "a": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/EmmsbFNP7oHbBrxQ8zocbKWmnAh",
    "b": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/QXrKbsNjtoOwbOxWt8Lctc6YnEU",
    "c": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/VglFbzGQ8okQsFxBLDicxZVwnaf",
    "d": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/UST4bbrEQowEyWxgqYpcSMlknub",
    "home": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/QfJeb1X3GobTlPxANpGc5VMJnac",
    "family": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/SgEJbqufbo3Hd4xKJy1cj6P9ngf",
    "shrimp": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/K7Rnbwl8XowMqRx9lkRcx2l6n5f",
    "plated": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/XyEwbDYoRoZ5oHxt2UxcWDcpne0",
    "yue": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/OuxYbiOzLoksW7x6nbJckiUqnth",
    "light": "https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/IjGhbYaBdoSvgSx3X45cAMgYn1g",
}

CHEF_SEED = [
    dict(id=1, name="王建军", avatar=IMG["a"], cuisine="徽菜", rating=4.9, order_count=128,
         distance=2.1, base_price=168, categories=["家常三餐", "家宴请客"],
         tags=["徽菜私厨", "10 年经验", "少油少盐"], badges=["健康证", "背景核验"],
         bio="安徽黄山人，从业 12 年，擅长徽州家常菜与家宴，火候稳、口味家常。",
         hero=IMG["hero"]),
    dict(id=2, name="李静", avatar=IMG["b"], cuisine="母婴营养餐", rating=4.9, order_count=96,
         distance=1.4, base_price=198, categories=["月子餐", "轻食减脂"],
         tags=["营养师", "月子餐", "低脂轻食"], badges=["营养师证", "健康证"],
         bio="注册营养师，服务过 40+ 产后家庭，擅长分阶段月子餐与低卡轻食。",
         hero=IMG["yue"]),
    dict(id=3, name="陈志远", avatar=IMG["c"], cuisine="粤菜 · 五星酒店", rating=5.0, order_count=214,
         distance=3.8, base_price=398, categories=["家宴请客", "家常三餐"],
         tags=["五星酒店主厨", "粤菜", "位上摆盘"], badges=["高级技师", "健康证", "背景核验"],
         bio="广州五星酒店行政主厨出身，擅长粤式家宴与位上摆盘，请客体面。",
         hero=IMG["plated"]),
    dict(id=4, name="张立", avatar=IMG["d"], cuisine="川湘菜", rating=4.8, order_count=73,
         distance=4.6, base_price=188, categories=["家常三餐", "家宴请客"],
         tags=["川湘菜", "重口可调", "快手家常"], badges=["健康证"],
         bio="川湘菜出身，擅长下饭菜与辣度分级，口味可按家庭偏好调整。",
         hero=IMG["shrimp"]),
]

PACKAGE_SEED = [
    (1, "四菜一汤 · 家常", "两荤两素一汤，适合 3–4 人日常晚餐", 268, 100, 90, 0, IMG["home"]),
    (1, "六菜一汤 · 待客", "三荤三素一汤，适合 5–6 人小聚", 398, 160, 120, 1, IMG["family"]),
    (2, "月子餐 · 三阶段", "按产后周数配餐，少盐少油高蛋白", 398, 180, 100, 0, IMG["yue"]),
    (2, "轻食减脂 · 五餐", "低卡高蛋白五日餐，含热量标注", 298, 120, 80, 1, IMG["light"]),
    (3, "四菜一汤 · 粤式", "清蒸、煲汤为主，食材讲究", 298, 120, 90, 0, IMG["home"]),
    (3, "八菜一汤 · 家宴", "含位上摆盘与餐后清洁，适合 8–10 人", 698, 260, 150, 1, IMG["plated"]),
    (4, "四菜一汤 · 川湘", "香辣下饭，辣度可调", 258, 90, 85, 0, IMG["shrimp"]),
    (4, "六菜一汤 · 家宴", "含两道硬菜，适合 6 人", 388, 150, 120, 1, IMG["family"]),
]

STAT_CATS = [
    ("家常三餐", "家常", "🍲"),
    ("家宴请客", "家宴", "🥂"),
    ("月子餐", "月子", "👶"),
    ("轻食减脂", "轻食", "🥗"),
]

SLOTS = ["11:00-13:00", "17:30-19:30", "18:00-20:00"]


def now_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def today() -> date:
    return datetime.now(TZ).date()


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    d = os.path.dirname(os.path.abspath(DB_PATH))
    if d:
        os.makedirs(d, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) n FROM chefs").fetchone()["n"] == 0:
            for ch in CHEF_SEED:
                c.execute(
                    "INSERT INTO chefs(id,name,avatar,cuisine,rating,order_count,distance,base_price,tags,badges,categories,bio,hero)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ch["id"], ch["name"], ch["avatar"], ch["cuisine"], ch["rating"], ch["order_count"],
                     ch["distance"], ch["base_price"], json.dumps(ch["tags"], ensure_ascii=False),
                     json.dumps(ch["badges"], ensure_ascii=False), json.dumps(ch["categories"], ensure_ascii=False),
                     ch["bio"], ch["hero"]),
                )
            for i, (cid, name, desc, price, food, minutes, sort, img) in enumerate(PACKAGE_SEED, start=1):
                c.execute("INSERT INTO packages(id,chef_id,name,desc,price,food_cost,minutes,sort,cover)"
                          " VALUES(?,?,?,?,?,?,?,?,?)",
                          (i, cid, name, desc, price, food, minutes, sort, img))
        # 迁移：补 cover 列
        cols = [r["name"] for r in c.execute("PRAGMA table_info(packages)").fetchall()]
        if "cover" not in cols:
            c.execute("ALTER TABLE packages ADD COLUMN cover TEXT")
            for i, row in enumerate(PACKAGE_SEED, start=1):
                c.execute("UPDATE packages SET cover=? WHERE id=?", (row[7], i))


# ---------------------------------------------------------------- Token
def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id: int) -> str:
    payload = json.dumps({"uid": user_id, "exp": int(time.time()) + TOKEN_TTL}).encode()
    body = b64e(payload)
    sig = b64e(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(token: str) -> int:
    try:
        body, sig = token.split(".")
        expect = b64e(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            raise ValueError("bad sig")
        payload = json.loads(b64d(body))
        if payload["exp"] < time.time():
            raise ValueError("expired")
        return int(payload["uid"])
    except Exception:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")


def current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    uid = parse_token(authorization.split(" ", 1)[1])
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="用户不存在")
    return dict(row)


# ---------------------------------------------------------------- 用户初始化（演示订单/消息）
def bootstrap_user(c: sqlite3.Connection, user: dict) -> None:
    uid = user["id"]
    if c.execute("SELECT COUNT(*) n FROM orders WHERE user_id=?", (uid,)).fetchone()["n"] > 0:
        return
    day = today()
    demo = [
        # 服务中：李静 · 月子餐
        dict(status="serving", pkg=3, chef=2, d=day, slot="17:30-19:30", people=3,
             tastes=["少油少盐"], demo=1),
        # 待评价：陈志远 · 粤式四菜一汤
        dict(status="to_review", pkg=5, chef=3, d=day - timedelta(days=3), slot="18:00-20:00", people=6,
             tastes=["清淡", "软烂"], demo=1),
        # 已完成并已评价
        dict(status="done", pkg=1, chef=1, d=day - timedelta(days=12), slot="17:30-19:30", people=4,
             tastes=["少油少盐"], demo=1, reviewed=1),
        dict(status="done", pkg=6, chef=3, d=day - timedelta(days=40), slot="17:30-19:30", people=8,
             tastes=[], demo=1, reviewed=1),
        dict(status="done", pkg=7, chef=4, d=day - timedelta(days=75), slot="18:00-20:00", people=4,
             tastes=["忌辣"], demo=1, reviewed=1),
        dict(status="done", pkg=4, chef=2, d=day - timedelta(days=130), slot="17:30-19:30", people=4,
             tastes=[], demo=1, reviewed=1),
    ]
    for item in demo:
        create_order_row(c, user, dict(
            chef_id=item["chef"], package_id=item["pkg"], service_date=item["d"].isoformat(),
            slot=item["slot"], people=item["people"], tastes=item["tastes"],
            address="郑州市金水区农业路 12 号 绿地老街 3 号楼 2 单元 1802",
            contact="张晓宁", note="", status=item["status"], demo=item["demo"],
            reviewed=item.get("reviewed", 0),
        ))
    msgs = [
        ("system", "欢迎使用主厨到家", "完善地址与口味偏好，厨师上门会更顺手。", 0),
        ("promo", "新客立减 30 元", "本月预约家宴套餐，服务费满 600 减 30，限前 200 单。", 0),
        ("service", "厨师资质已核验", "平台厨师均完成健康证与背景核验，可放心下单。", 1),
        ("service", "账单已结算", "上一单费用已结算完成，食材多退少补差额已原路退回。", 1),
    ]
    for cat, title, body, read in msgs:
        c.execute("INSERT INTO messages(user_id,category,title,body,read,created_at) VALUES(?,?,?,?,?,?)",
                  (uid, cat, title, body, read, now_str()))
    c.commit()


def create_order_row(c, user, p, status: Optional[str] = None, demo: int = 0,
                     reviewed: int = 0, total_override: Optional[int] = None) -> str:
    status = status or p.get("status", "pending")
    reviewed = p.get("reviewed", reviewed)
    chef = c.execute("SELECT * FROM chefs WHERE id=?", (p["chef_id"],)).fetchone()
    pkg = c.execute("SELECT * FROM packages WHERE id=?", (p["package_id"],)).fetchone()
    if not chef or not pkg:
        raise ValueError("厨师或套餐不存在")
    rid = "CZ" + (p["service_date"][2:4] + p["service_date"][5:7] + p["service_date"][8:10]) + \
          "".join(random.choices(string.digits, k=4))
    service_fee = pkg["price"]
    food_fee = pkg["food_cost"]
    total = total_override if total_override is not None else service_fee + food_fee + PLATFORM_FEE
    c.execute("INSERT INTO orders(id,user_id,chef_id,package_id,service_date,slot,people,tastes,address,contact,note,"
              "service_fee,food_fee,platform_fee,total,status,created_at,demo,reviewed)"
              " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (rid, user["id"], p["chef_id"], p["package_id"], p["service_date"], p["slot"], p["people"],
               json.dumps(p["tastes"], ensure_ascii=False), p["address"], p["contact"], p.get("note", ""),
               service_fee, food_fee, PLATFORM_FEE, total, status, now_str(), demo, reviewed))
    build_events(c, rid, status, chef["name"], p["slot"], now_str())
    return rid


def build_events(c, order_id: str, status: str, chef_name: str, slot: str, created: str) -> None:
    hm = created[11:16] if len(created) >= 16 else created
    try:
        hh, mm = [int(x) for x in slot.split("-")[0].split(":")]
        arrive = f"{(hh * 60 + mm - 40) // 60:02d}:{(hh * 60 + mm - 40) % 60:02d}"
    except Exception:
        arrive = "开始前 40 分钟"
    if status == "pending":
        events = [("预约已提交", hm, "订单创建成功，已通知厨师接单", 1),
                  ("厨师已接单", hm, f"{chef_name}已确认，食材清单已发送", 1),
                  ("厨师出发", "预计 " + arrive, "出发采购食材，费用按实际结算", 0),
                  ("上门做饭", slot.split("-")[0], "约 90 分钟，含餐后清洁", 0)]
    elif status == "serving":
        events = [("预约已提交", hm, "订单创建成功", 1),
                  ("厨师已接单", hm, f"{chef_name}已确认", 1),
                  ("厨师到达", hm, "已到达并开始备菜", 1),
                  ("上门做饭", slot.split("-")[0], "约 90 分钟，含餐后清洁", 0)]
    elif status in ("to_review", "done"):
        events = [("预约已提交", hm, "订单创建成功", 1),
                  ("厨师已接单", hm, f"{chef_name}已确认", 1),
                  ("厨师到达", hm, "已到达并开始备菜", 1),
                  ("服务完成", hm, "菜品已上桌，餐后清洁完成", 1)]
    else:
        events = [("预约已提交", hm, "订单创建成功", 1),
                  ("订单已取消", hm, "用户主动取消，费用原路退回", 1)]
    for i, (t, at, d, ok) in enumerate(events):
        c.execute("INSERT INTO order_events(order_id,title,detail,at,done,sort) VALUES(?,?,?,?,?,?)",
                  (order_id, t, d, at, ok, i))


# ---------------------------------------------------------------- 序列化
def chef_dict(row) -> dict:
    return dict(id=row["id"], name=row["name"], avatar=row["avatar"], cuisine=row["cuisine"],
                rating=row["rating"], orders=row["order_count"], distance=row["distance"],
                basePrice=row["base_price"], tags=json.loads(row["tags"]),
                badges=json.loads(row["badges"]), categories=json.loads(row["categories"]),
                bio=row["bio"], hero=row["hero"])


def pkg_dict(row) -> dict:
    return dict(id=row["id"], chefId=row["chef_id"], name=row["name"], desc=row["desc"],
                price=row["price"], foodCost=row["food_cost"], minutes=row["minutes"],
                cover=row["cover"] if "cover" in row.keys() else "")


def order_dict(c, row) -> dict:
    chef = c.execute("SELECT * FROM chefs WHERE id=?", (row["chef_id"],)).fetchone()
    pkg = c.execute("SELECT * FROM packages WHERE id=?", (row["package_id"],)).fetchone()
    events = [dict(title=e["title"], detail=e["detail"], at=e["at"], done=bool(e["done"]))
              for e in c.execute("SELECT * FROM order_events WHERE order_id=? ORDER BY sort", (row["id"],))]
    return dict(
        id=row["id"], status=row["status"], statusText=STATUS_TEXT.get(row["status"], row["status"]),
        chef=dict(id=chef["id"], name=chef["name"], avatar=chef["avatar"], cuisine=chef["cuisine"],
                  rating=chef["rating"], orders=chef["order_count"]),
        package=dict(id=pkg["id"], name=pkg["name"], cover=pkg["cover"] or "", minutes=pkg["minutes"]),
        date=row["service_date"], slot=row["slot"], people=row["people"],
        tastes=json.loads(row["tastes"]), address=row["address"], contact=row["contact"],
        note=row["note"], serviceFee=row["service_fee"], foodFee=row["food_fee"],
        platformFee=row["platform_fee"], total=row["total"], createdAt=row["created_at"],
        reviewed=bool(row["reviewed"]), events=events,
    )


# ---------------------------------------------------------------- 请求体
class CodeIn(BaseModel):
    phone: str


class LoginIn(BaseModel):
    phone: str
    code: str


class OrderIn(BaseModel):
    chefId: int
    packageId: int
    date: str
    slot: str
    people: int = Field(ge=1, le=12)
    tastes: List[str] = []
    address: str
    contact: str
    note: str = ""


class ReviewIn(BaseModel):
    rating: float = Field(ge=1, le=5)
    tags: List[str] = []
    content: str = ""
    anonymous: bool = False


app = FastAPI(title="主厨到家 API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    init_db()


# ---------------------------------------------------------------- 鉴权
@app.post("/api/auth/code")
def send_code(body: CodeIn):
    if not re.fullmatch(r"1[3-9]\d{9}", body.phone):
        raise HTTPException(400, "手机号格式不正确")
    code = "".join(random.choices(string.digits, k=6))
    with conn() as c:
        c.execute("INSERT INTO codes(phone,code,expires_at) VALUES(?,?,?) "
                  "ON CONFLICT(phone) DO UPDATE SET code=excluded.code, expires_at=excluded.expires_at",
                  (body.phone, code, time.time() + 300))
        c.commit()
    if DEV_MODE:      # 生产环境在此接入短信服务商，不要回传验证码
        return {"ok": True, "devCode": code, "expireIn": 300}
    return {"ok": True, "expireIn": 300}


@app.post("/api/auth/login")
def login(body: LoginIn):
    with conn() as c:
        row = c.execute("SELECT * FROM codes WHERE phone=?", (body.phone,)).fetchone()
        if not row or row["expires_at"] < time.time():
            raise HTTPException(400, "验证码已过期，请重新获取")
        if row["code"] != body.code:
            raise HTTPException(400, "验证码不正确")
        user = c.execute("SELECT * FROM users WHERE phone=?", (body.phone,)).fetchone()
        if not user:
            c.execute("INSERT INTO users(phone,nickname,avatar,level,points,created_at) VALUES(?,?,?,?,?,?)",
                      (body.phone, "张晓宁", "", 3, 1280, now_str()))
            user = c.execute("SELECT * FROM users WHERE phone=?", (body.phone,)).fetchone()
        c.execute("DELETE FROM codes WHERE phone=?", (body.phone,))
        c.commit()
        u = dict(user)
        bootstrap_user(c, u)
    return {"token": make_token(u["id"]), "user": dict(
        id=u["id"], phone=u["phone"], nickname=u["nickname"], level=u["level"], points=u["points"])}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return dict(id=user["id"], phone=user["phone"], nickname=user["nickname"],
                level=user["level"], points=user["points"])


# ---------------------------------------------------------------- 首页 / 厨师
@app.get("/api/home")
def home(cat: Optional[str] = None):
    with conn() as c:
        rows = c.execute("SELECT * FROM chefs ORDER BY rating DESC").fetchall()
        chefs = [chef_dict(r) for r in rows]
        if cat:
            chefs = [x for x in chefs if cat in x["categories"]]
        all_pkgs = c.execute("SELECT * FROM packages ORDER BY sort, price DESC").fetchall()
        pkgs = [pkg_dict(r) for r in all_pkgs]
        if cat:
            ids = {x["id"] for x in chefs}
            pkgs = [p for p in pkgs if p["chefId"] in ids]
            pkgs.sort(key=lambda p: -p["price"])
        else:
            pkgs = sorted(pkgs, key=lambda p: (p["chefId"], p["id"]))
    return dict(
        categories=[dict(key=k, short=s, icon=i) for k, s, i in STAT_CATS],
        chefs=chefs, packages=pkgs,
        banner=dict(title="家宴请客", sub="五星酒店主厨上门 · 位上摆盘", image=IMG["plated"]),
        stats=dict(chefs=len(rows), orders=sum(x["orders"] for x in chefs), rating=4.92,
                   verified=len(chefs)),
    )


@app.get("/api/chefs")
def chefs(cat: Optional[str] = None, sort: str = "rating"):
    with conn() as c:
        rows = c.execute("SELECT * FROM chefs").fetchall()
    items = [chef_dict(r) for r in rows]
    if cat:
        items = [x for x in items if cat in x["categories"]]
    if sort == "distance":
        items.sort(key=lambda x: x["distance"])
    elif sort == "price":
        items.sort(key=lambda x: x["basePrice"])
    else:
        items.sort(key=lambda x: -x["rating"])
    return dict(items=items, total=len(items))


@app.get("/api/chefs/{chef_id}")
def chef_detail(chef_id: int):
    with conn() as c:
        row = c.execute("SELECT * FROM chefs WHERE id=?", (chef_id,)).fetchone()
        if not row:
            raise HTTPException(404, "厨师不存在")
        pkgs = c.execute("SELECT * FROM packages WHERE chef_id=? ORDER BY sort, price DESC", (chef_id,)).fetchall()
        reviews = c.execute("SELECT * FROM reviews WHERE chef_id=? ORDER BY id DESC LIMIT 5", (chef_id,)).fetchall()
    d = chef_dict(row)
    d["packages"] = [pkg_dict(p) for p in pkgs]
    d["reviews"] = [dict(rating=r["rating"], content=r["content"],
                         tags=json.loads(r["tags"]), anonymous=bool(r["anonymous"]),
                         at=r["created_at"], user="匿名用户" if r["anonymous"] else "张**") for r in reviews]
    if not d["reviews"]:
        d["reviews"] = [dict(rating=5.0, content="菜做得很家常，收拾得也干净，下次还约。",
                             tags=["准时到达", "餐后清洁"], anonymous=True, at="", user="匿名用户")]
    return d


@app.get("/api/availability")
def availability(chef_id: int, date_str: str = Query(alias="date")):
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
    with conn() as c:
        booked = {r["slot"] for r in c.execute(
            "SELECT slot FROM orders WHERE chef_id=? AND service_date=? AND status NOT IN ('canceled')",
            (chef_id, date_str))}
    out = []
    for s in SLOTS:
        disabled = s in booked or (s == "11:00-13:00" and d == today())
        out.append(dict(slot=s, disabled=bool(disabled),
                        reason="已被预约" if s in booked else ("已约满" if disabled else "")))
    return dict(date=date_str, slots=out)


@app.get("/api/dates")
def dates():
    d0 = today()
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    items = []
    for i in range(4):
        d = d0 + timedelta(days=i)
        label = "今天" if i == 0 else ("明天" if i == 1 else names[d.isoweekday() - 1])
        items.append(dict(date=d.isoformat(), day=label, mmdd=d.strftime("%m-%d")))
    return dict(items=items)


# ---------------------------------------------------------------- 订单
@app.post("/api/orders")
def create_order(body: OrderIn, user=Depends(current_user)):
    if not body.address.strip():
        raise HTTPException(400, "请填写服务地址")
    try:
        d = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(400, "日期格式不正确")
    if d < today():
        raise HTTPException(400, "不能预约过去的日期")
    with conn() as c:
        chef = c.execute("SELECT * FROM chefs WHERE id=?", (body.chefId,)).fetchone()
        pkg = c.execute("SELECT * FROM packages WHERE id=? AND chef_id=?", (body.packageId, body.chefId)).fetchone()
        if not chef or not pkg:
            raise HTTPException(400, "厨师或套餐不存在")
        taken = c.execute("SELECT 1 FROM orders WHERE chef_id=? AND service_date=? AND slot=? AND status!='canceled'",
                          (body.chefId, body.date, body.slot)).fetchone()
        if taken:
            raise HTTPException(409, "该时段已被预约，请换一个时段")
        rid = create_order_row(c, user, dict(
            chef_id=body.chefId, package_id=body.packageId, service_date=body.date, slot=body.slot,
            people=body.people, tastes=body.tastes, address=body.address.strip(),
            contact=body.contact.strip() or user["nickname"], note=body.note))
        c.execute("INSERT INTO messages(user_id,category,title,body,read,created_at) VALUES(?,?,?,?,?,?)",
                  (user["id"], "order", "预约已提交", f"{chef['name']} · {pkg['name']}，{body.date} {body.slot} 上门，已通知厨师接单。", 0, now_str()))
        c.commit()
        row = c.execute("SELECT * FROM orders WHERE id=?", (rid,)).fetchone()
        return order_dict(c, row)


@app.get("/api/orders")
def list_orders(status: str = "all", user=Depends(current_user)):
    with conn() as c:
        if status == "all":
            rows = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY service_date DESC, created_at DESC",
                             (user["id"],)).fetchall()
        elif status == "to_review":
            rows = c.execute("SELECT * FROM orders WHERE user_id=? AND status IN ('to_review','done') AND reviewed=0"
                             " ORDER BY service_date DESC", (user["id"],)).fetchall()
        else:
            rows = c.execute("SELECT * FROM orders WHERE user_id=? AND status=? ORDER BY service_date DESC",
                             (user["id"], status)).fetchall()
        items = [order_dict(c, r) for r in rows]
    counts = {k: 0 for k in ["pending", "serving", "to_review", "done", "all"]}
    for it in items:
        counts["all"] += 1
        if it["status"] in counts:
            counts[it["status"]] += 1
        if it["status"] == "done" and not it["reviewed"]:
            counts["to_review"] += 1
    return dict(items=items, counts=counts)


@app.get("/api/orders/{order_id}")
def order_detail(order_id: str, user=Depends(current_user)):
    with conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "订单不存在")
        return order_dict(c, row)


@app.post("/api/orders/{order_id}/cancel")
def cancel_order(order_id: str, user=Depends(current_user)):
    with conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "订单不存在")
        if row["status"] not in ("pending", "serving"):
            raise HTTPException(409, "当前状态不可取消")
        c.execute("UPDATE orders SET status='canceled' WHERE id=?", (order_id,))
        c.execute("DELETE FROM order_events WHERE order_id=?", (order_id,))
        build_events(c, order_id, "canceled", "", "", now_str())
        c.execute("INSERT INTO messages(user_id,category,title,body,read,created_at) VALUES(?,?,?,?,?,?)",
                  (user["id"], "order", "订单已取消", f"订单 {order_id} 已取消，费用将原路退回。", 0, now_str()))
        c.commit()
        return dict(ok=True, refund=row["total"])


@app.post("/api/orders/{order_id}/review")
def review(order_id: str, body: ReviewIn, user=Depends(current_user)):
    with conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "订单不存在")
        if row["status"] not in ("to_review", "done"):
            raise HTTPException(409, "订单完成后才能评价")
        if row["reviewed"]:
            raise HTTPException(409, "该订单已评价")
        c.execute("INSERT INTO reviews(order_id,user_id,chef_id,rating,tags,content,anonymous,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (order_id, user["id"], row["chef_id"], body.rating, json.dumps(body.tags, ensure_ascii=False),
                   body.content, 1 if body.anonymous else 0, now_str()))
        c.execute("UPDATE orders SET reviewed=1 WHERE id=?", (order_id,))
        c.commit()
        return dict(ok=True)


# ---------------------------------------------------------------- 运营端（厨师接单状态推进）
class StatusIn(BaseModel):
    status: str = Field(pattern="^(pending|serving|to_review|done|canceled)$")


@app.get("/api/ops/backup")
def ops_backup(x_admin_token: str = Header(default="")):
    """下载数据库快照，用于免费平台无持久盘时的手工备份。"""
    if x_admin_token != os.environ.get("ADMIN_TOKEN", "ops-2026"):
        raise HTTPException(401, "运营端口令不正确")
    import tempfile
    tmp = tempfile.mktemp(suffix=".db")
    with conn() as c:
        c.execute("VACUUM INTO ?", (tmp,))
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M")
    return FileResponse(tmp, filename=f"zhuchu-{stamp}.db", media_type="application/octet-stream")


@app.post("/api/ops/orders/{order_id}/status")
def ops_set_status(order_id: str, body: StatusIn, x_admin_token: str = Header(default="")):
    """运营/厨师端推进订单状态：需在请求头带 X-Admin-Token。"""
    if x_admin_token != os.environ.get("ADMIN_TOKEN", "ops-2026"):
        raise HTTPException(401, "运营端口令不正确")
    with conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "订单不存在")
        chef = c.execute("SELECT name FROM chefs WHERE id=?", (row["chef_id"],)).fetchone()
        c.execute("UPDATE orders SET status=? WHERE id=?", (body.status, order_id))
        c.execute("DELETE FROM order_events WHERE order_id=?", (order_id,))
        build_events(c, order_id, body.status, chef["name"], row["slot"], now_str())
        c.execute("INSERT INTO messages(user_id,category,title,body,read,created_at) VALUES(?,?,?,?,?,?)",
                  (row["user_id"], "order", STATUS_TEXT[body.status],
                   f"订单 {order_id} 状态更新为「{STATUS_TEXT[body.status]}」。", 0, now_str()))
        c.commit()
        return dict(ok=True, id=order_id, status=body.status)


# ---------------------------------------------------------------- 消息
@app.get("/api/messages")
def messages(category: str = "all", user=Depends(current_user)):
    with conn() as c:
        if category == "all":
            rows = c.execute("SELECT * FROM messages WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
        else:
            rows = c.execute("SELECT * FROM messages WHERE user_id=? AND category=? ORDER BY id DESC",
                             (user["id"], category)).fetchall()
        unread = c.execute("SELECT COUNT(*) n FROM messages WHERE user_id=? AND read=0",
                           (user["id"],)).fetchone()["n"]
        counts = {r["category"]: r["n"] for r in c.execute(
            "SELECT category, COUNT(*) n FROM messages WHERE user_id=? GROUP BY category", (user["id"],))}
        total = c.execute("SELECT COUNT(*) n FROM messages WHERE user_id=?", (user["id"],)).fetchone()["n"]
    counts["all"] = total
    return dict(items=[dict(id=r["id"], category=r["category"], title=r["title"], body=r["body"],
                            read=bool(r["read"]), at=r["created_at"]) for r in rows],
                counts=counts, unread=unread)


@app.post("/api/messages/read-all")
def read_all(user=Depends(current_user)):
    with conn() as c:
        c.execute("UPDATE messages SET read=1 WHERE user_id=?", (user["id"],))
        c.commit()
    return dict(ok=True)


# ---------------------------------------------------------------- 统计
@app.get("/api/stats")
def stats(range_: str = Query("6m", alias="range"), user=Depends(current_user)):
    months_needed = {"1m": 1, "6m": 6, "y": 12}.get(range_, 6)
    with conn() as c:
        rows = c.execute("SELECT * FROM orders WHERE user_id=? AND status!='canceled'", (user["id"],)).fetchall()
    d0 = today()
    buckets = []
    for i in range(months_needed - 1, -1, -1):
        y, m = d0.year, d0.month - i
        while m <= 0:
            m += 12
            y -= 1
        buckets.append((y, m, f"{m}月"))
    spend = {b[1]: 0 for b in buckets}
    counts = {b[1]: 0 for b in buckets}
    cuisine = {}
    months_set = {(b[0], b[1]) for b in buckets}
    for r in rows:
        try:
            od = date.fromisoformat(r["service_date"])
        except Exception:
            continue
        if (od.year, od.month) in months_set:
            spend[od.month] += r["total"]
            counts[od.month] += 1
    with conn() as c:
        for r in rows:
            ch = c.execute("SELECT cuisine FROM chefs WHERE id=?", (r["chef_id"],)).fetchone()
            if ch:
                key = ch["cuisine"].split("·")[0].strip()
                cuisine[key] = cuisine.get(key, 0) + 1
    total_spend = sum(r["total"] for r in rows)
    total_orders = len(rows)
    hours = 0
    with conn() as c:
        for r in rows:
            pkg = c.execute("SELECT minutes FROM packages WHERE id=?", (r["package_id"],)).fetchone()
            if pkg:
                hours += pkg["minutes"] / 60
    bars = [dict(label=b[2], value=spend[b[1]], orders=counts[b[1]]) for b in buckets]
    maxv = max([b["value"] for b in bars] + [1])
    for b in bars:
        b["pct"] = round(b["value"] / maxv * 100, 1)
    pref = sorted([dict(name=k, count=v, pct=round(v / max(total_orders, 1) * 100))
                   for k, v in cuisine.items()], key=lambda x: -x["count"])[:4]
    avg = round(total_spend / total_orders) if total_orders else 0
    return dict(range=range_, totalSpend=total_spend, totalOrders=total_orders,
                hours=round(hours, 1), avg=avg, bars=bars,
                preference=pref, saved=round(total_spend * 0.35), points=user["points"])


# ---------------------------------------------------------------- 静态资源
@app.get("/healthz")
def healthz():
    return dict(ok=True, ts=now_str())





# ---------------------------------------------------------------- 内嵌前端（单文件部署）
WEB_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1">
<meta name="theme-color" content="#12293D">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="主厨到家">

<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>主厨到家 · 上门私厨预约</title>
<style>
:root{
  --navy:#12293D;--navy2:#1B3B55;--ink:#14202C;--ink2:#47586A;--ink3:#8B99A8;--ink4:#A6B2BF;
  --line:#E7ECF1;--bg:#F5F7F9;--brass:#B98B52;--brass2:#C6A265;--brass-t:#9C7134;--brass-bg:#FAF5EC;
  --green:#2C7257;--red:#B0473C;
  --sat:env(safe-area-inset-top);--sab:env(safe-area-inset-bottom);
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{background:#E9EDF1;font-family:"Noto Sans SC","PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:var(--ink);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;font-feature-settings:"tnum"}
#app{position:relative;height:100dvh;max-width:430px;margin:0 auto;background:#fff;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 0 60px rgba(14,32,48,.18)}
img{display:block}
.ic{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linejoin:round;stroke-linecap:round;flex:0 0 auto}
.ic.s{width:16px;height:16px;stroke-width:1.8}
.ic.xs{width:13px;height:13px;stroke-width:2}
.ic.fill{fill:currentColor;stroke:none}
.view{flex:1;min-height:0;display:flex;flex-direction:column;animation:fade .2s ease both}
@keyframes fade{from{opacity:.4;transform:translateY(5px)}to{opacity:1;transform:none}}
.scroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
.pad{padding:0 20px}
.topbar{display:flex;align-items:center;gap:10px;padding:calc(var(--sat) + 10px) 16px 10px;background:#fff;position:relative;z-index:5}
.topbar b{font-size:16.5px;font-weight:600}
.topbar .sp{flex:1}
.btn-p{height:50px;border-radius:12px;background:var(--navy);color:#fff;font-size:15.5px;font-weight:600;display:flex;align-items:center;justify-content:center;border:0;width:100%;letter-spacing:.04em}
.btn-p:active{transform:scale(.985);opacity:.94}
.btn-g{height:44px;border-radius:10px;border:1px solid #DCE3EA;background:#fff;color:#31465A;font-size:13px;font-weight:500;display:flex;align-items:center;justify-content:center}
.mini{height:30px;padding:0 14px;border-radius:6px;background:var(--navy);color:#fff;font-size:12px;font-weight:500;display:inline-flex;align-items:center}
.pill{display:inline-flex;align-items:center;gap:4px;height:21px;padding:0 8px;border-radius:4px;font-size:10.5px;font-weight:600}
.pill.nv{background:#EAEFF4;color:#1B3B55}.pill.br{background:#F6EFE3;color:var(--brass-t)}
.pill.gr{background:#E8F1EC;color:var(--green)}.pill.rd{background:#FAEDEB;color:#A8493E}
.chip{display:inline-flex;align-items:center;justify-content:center;height:30px;padding:0 13px;border-radius:15px;background:#F2F5F8;color:#5A6B7D;font-size:12.5px;white-space:nowrap;border:1px solid transparent;transition:.15s}
.chip.on{background:var(--navy);color:#fff;font-weight:500}
.chip.line{background:#fff;border-color:#E1E7EE}
.px{color:var(--brass-t);font-weight:600;font-size:15px}
.px i{font-style:normal;font-size:10px;font-weight:500;margin-left:1px}
.sub{font-size:12.5px;color:var(--ink3)}
.card{background:#fff;border-radius:12px;border:1px solid var(--line);padding:13px 14px}
.sec-head{display:flex;align-items:center;justify-content:space-between;margin:18px 0 12px}
.sec-head b{font-size:15.5px;font-weight:600}
.more{font-size:11.5px;color:var(--ink3);display:flex;align-items:center;gap:2px}
.tabbar{display:flex;background:#fff;border-top:1px solid #EDF1F5;padding:9px 0 calc(8px + var(--sab))}
.tabbar div{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;font-size:10.5px;color:#96A3B1}
.tabbar div.on{color:var(--navy);font-weight:600}
.tabbar .ic{width:23px;height:23px}
/* splash */
.splash{position:relative;flex:1;display:flex;flex-direction:column;overflow:hidden;background:#0A1420}
.splash img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.veil{position:absolute;inset:0;background:linear-gradient(180deg,rgba(8,19,28,.75) 0%,rgba(8,19,28,.55) 30%,rgba(8,19,28,.78) 62%,rgba(8,19,28,.98) 100%)}
.sp-body{position:relative;margin:auto;text-align:center;color:#fff;padding:0 24px calc(120px + var(--sab))}
.mark{width:58px;height:58px;margin:0 auto 26px;border:1px solid rgba(255,255,255,.42);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:25px;color:#E3C892;background:rgba(255,255,255,.06)}
.sp-body h1{font-size:36px;font-weight:600;letter-spacing:.22em;text-indent:.22em}
.en{font-size:10px;letter-spacing:.34em;color:rgba(255,255,255,.62);margin-top:12px}
.rule{width:36px;height:1px;background:var(--brass2);margin:22px auto}
.tagline{font-size:13.5px;color:rgba(255,255,255,.9);letter-spacing:.14em}
.enter{margin:44px auto 0;height:46px;padding:0 34px;border:1px solid rgba(255,255,255,.4);border-radius:24px;color:#fff;font-size:13.5px;display:inline-flex;align-items:center;gap:8px;letter-spacing:.12em;background:rgba(255,255,255,.08);backdrop-filter:blur(6px);animation:pulse 2.4s infinite}
@keyframes pulse{0%,100%{opacity:.85}50%{opacity:1;box-shadow:0 0 0 6px rgba(255,255,255,.05)}}
.sp-foot{position:absolute;left:0;right:0;bottom:calc(36px + var(--sab));text-align:center;color:rgba(255,255,255,.5);font-size:10.5px;line-height:1.9}
/* login */
.field{height:56px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;font-size:15px}
.field .pre{font-size:14px;color:#31465A;font-weight:500}
.field .sep{width:1px;height:18px;background:#E3E9EF}
.field input{flex:1;border:0;outline:0;font-size:15px;font-family:inherit;letter-spacing:.04em;background:none;min-width:0}
.field .code{color:var(--brass-t);font-size:12.5px;font-weight:600;white-space:nowrap}
.field .code.off{color:var(--ink4)}
.h-xl{font-size:27px;font-weight:700;letter-spacing:-.3px}
.agree{display:flex;gap:7px;align-items:flex-start;font-size:11.5px;color:var(--ink3);margin-top:16px;line-height:1.7}
.box{width:14px;height:14px;border-radius:3px;border:1px solid #C9D3DE;flex:0 0 auto;margin-top:2px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:9px}
.box.on{background:var(--navy);border-color:var(--navy)}
.divider{display:flex;align-items:center;gap:14px;font-size:11px;color:var(--ink4);margin:30px 0 18px}
.divider i{flex:1;height:1px;background:#EDF1F5}
.oauth{display:flex;gap:16px;justify-content:center}
.o{width:74px;height:46px;border:1px solid #E1E7EE;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:12.5px;color:#5A6B7D}
.gift{background:var(--brass-bg);border:1px solid #EFE3CE;border-radius:12px;padding:14px 16px;display:flex;gap:12px;align-items:center}
.gift b{font-size:13.5px;color:#7A5521;font-weight:600}
.gift p{font-size:11.5px;color:#A08454;margin-top:3px}
/* home */
.home-hd{background:var(--navy);color:#fff;padding:calc(var(--sat) + 10px) 0 18px;flex:0 0 auto}
.loc{display:flex;align-items:center;gap:6px;padding:0 20px;height:30px;font-size:13px}
.loc .bell{margin-left:auto;position:relative;display:flex}
.bdot{position:absolute;top:1px;right:-1px;width:6px;height:6px;border-radius:50%;background:#D9A45E}
.searchbar{margin:8px 20px 0;height:44px;border-radius:10px;background:#fff;display:flex;align-items:center;gap:8px;padding:0 14px;color:var(--ink4);font-size:13.5px}
.searchbar .sbtn{margin-left:auto;font-size:12px;color:#fff;background:var(--navy2);padding:5px 12px;border-radius:5px}
.cats{display:flex;padding:20px 20px 2px;gap:6px}
.cat{flex:1;display:flex;flex-direction:column;align-items:center;gap:8px;font-size:12px;color:rgba(255,255,255,.9)}
.cat.on i{background:rgba(227,200,146,.22);border-color:rgba(227,200,146,.55);color:#F0D9B4}
.cat i{width:42px;height:42px;border-radius:50%;background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.14);display:flex;align-items:center;justify-content:center;color:#E3C892;transition:.2s}
.hero-b{margin:16px 20px 0;height:146px;border-radius:12px;overflow:hidden;position:relative}
.hero-b img{width:100%;height:100%;object-fit:cover}
.hero-b .hv{position:absolute;inset:0;background:linear-gradient(90deg,rgba(10,22,32,.86),rgba(10,22,32,.15))}
.hero-b .txt{position:absolute;left:18px;top:0;bottom:0;display:flex;flex-direction:column;justify-content:center;color:#fff}
.hero-b em{font-style:normal;font-size:10px;letter-spacing:.2em;color:#D9BE8C}
.hero-b b{font-size:17px;font-weight:600;margin:7px 0 6px}
.hero-b p{font-size:11.5px;color:rgba(255,255,255,.72)}
.hero-b u{text-decoration:none;margin-top:12px;font-size:11px;color:#1B2C3E;background:#E7D2AC;align-self:flex-start;padding:5px 12px;border-radius:3px}
.chef-b{display:flex;gap:10px}
.cb{flex:1;background:#fff;border:1px solid var(--line);border-radius:12px;padding:11px;display:flex;gap:10px}
.cb img{width:48px;height:48px;border-radius:8px;object-fit:cover}
.cb .n{font-size:13px;font-weight:600}
.cb .m{font-size:10px;color:#96A3B1;margin-top:3px}
.cb .st{font-size:10.5px;color:var(--brass-t);font-weight:600;margin-top:5px}
.cb .st s{text-decoration:none;color:#96A3B1;font-weight:400;margin-left:4px}
.pkg{display:flex;gap:12px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:9px;margin-bottom:9px;align-items:center}
.pkg img{width:74px;height:56px;border-radius:8px;object-fit:cover}
.pkg div{flex:1;min-width:0}
.pkg b{font-size:13.5px;font-weight:600}
.pkg p{font-size:10.5px;color:#96A3B1;margin-top:4px}
/* list */
.filterbar{display:flex;gap:8px;padding:4px 18px 12px;background:#fff;align-items:center}
.sort{font-size:12px;color:#5A6B7D;padding:6px 2px;white-space:nowrap}
.sort.on{color:var(--navy);font-weight:600;position:relative}
.sort.on:after{content:"";position:absolute;left:50%;transform:translateX(-50%);bottom:0;width:14px;height:2px;background:var(--brass2);border-radius:2px}
.fl{width:34px;height:30px;border-radius:6px;background:#F2F5F8;display:flex;align-items:center;justify-content:center;margin-left:auto;color:#31465A}
.fchips{display:flex;gap:7px;padding:0 18px 12px;background:#fff;overflow-x:auto;border-bottom:1px solid #F0F3F7}
.fchips::-webkit-scrollbar{display:none}
.hint{font-size:11px;color:#96A3B1;margin:12px 0 10px}
.chef-c{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px;display:flex;gap:13px;margin-bottom:10px;transition:.15s}
.chef-c:active{transform:scale(.99);background:#FCFDFE}
.chef-c img{width:66px;height:66px;border-radius:10px;object-fit:cover}
.chef-c .b1{display:flex;align-items:center;gap:7px}
.chef-c .b1 b{font-size:15.5px;font-weight:600}
.chef-c .meta{font-size:11px;color:var(--ink3);margin-top:6px}
.chef-c .meta s{text-decoration:none;color:var(--brass-t);font-weight:600}
.tg{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.tg em{font-style:normal;font-size:10.5px;color:#5A6B7D;background:#F4F6F9;padding:3px 7px;border-radius:3px}
.chef-c .f1{display:flex;align-items:center;margin-top:11px;padding-top:10px;border-top:1px dashed #EEF1F5}
.dotok{font-size:10.5px;color:var(--green);display:flex;align-items:center;gap:4px;margin-left:12px}
.dotok i{width:5px;height:5px;border-radius:50%;background:var(--green);display:block}
.chef-c .f1 .mini{margin-left:auto}
/* detail */
.hero{position:relative;height:226px;flex:0 0 226px}
.hero img{width:100%;height:100%;object-fit:cover}
.hero .hv{position:absolute;inset:0;background:linear-gradient(180deg,rgba(8,19,28,.6),rgba(8,19,28,0) 38%,rgba(8,19,28,.25))}
.flt{position:absolute;top:calc(var(--sat) + 8px);left:0;right:0;display:flex;justify-content:space-between;padding:0 18px}
.circ{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.22);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;color:#fff;border:1px solid rgba(255,255,255,.3)}
.sheet{flex:1;background:#fff;border-radius:20px 20px 0 0;margin-top:-24px;position:relative;padding:18px 20px 0;display:flex;flex-direction:column;min-height:0}
.sh-head{display:flex;gap:14px}
.sh-head img{width:64px;height:64px;border-radius:12px;object-fit:cover}
.sh-head .n1{font-size:21px;font-weight:700;display:flex;align-items:center;gap:8px}
.badges{display:flex;gap:5px;margin-top:7px;flex-wrap:wrap}
.badges .pill{background:#F4F6F9;color:#5A6B7D;font-weight:500}
.stat{display:flex;margin-top:10px;align-items:center;gap:14px;font-size:11px;color:var(--ink3)}
.stat b{font-size:13px;color:var(--ink);font-weight:600}
.stat s{text-decoration:none;color:var(--brass2);font-weight:600}
.hr{height:1px;background:#EEF1F5;margin:13px 0}
.pk-row{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #F0F3F7;transition:.15s}
.pk-row:last-child{border-bottom:0}
.pk-row.on{background:#FBF8F3;border-radius:8px;padding-left:8px;padding-right:8px;border-bottom-color:transparent}
.pk-row .left{flex:1}
.pk-row b{font-size:14px;font-weight:600}
.pk-row p{font-size:11px;color:var(--ink3);margin-top:5px}
.rv{display:flex;gap:10px;padding:9px 0}
.rv .a{width:28px;height:28px;border-radius:50%;background:#E8EDF2;color:#4A5A6A;font-size:11px;display:flex;align-items:center;justify-content:center;font-weight:600}
.rv .c{flex:1}
.rv .t{display:flex;align-items:center;gap:8px;font-size:11.5px;color:#5A6B7D}
.rv .t b{font-size:12.5px;color:var(--ink)}
.rv p{font-size:11.5px;color:#5A6B7D;line-height:1.7;margin-top:5px}
.cta{display:flex;align-items:center;gap:14px;padding:10px 20px calc(10px + var(--sab));border-top:1px solid #EDF1F5;background:#fff}
/* booking */
.bk{padding:12px 18px 4px}
.blk{background:#fff;border-radius:12px;border:1px solid var(--line);padding:12px 14px;margin-bottom:8px}
.blk .hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:11px}
.blk .hd b{font-size:13.5px;font-weight:600}
.blk .hd span{font-size:11px;color:#96A3B1}
.mini-chef{display:flex;align-items:center;gap:10px;background:#fff;border-radius:12px;border:1px solid var(--line);padding:10px 14px;margin-bottom:8px}
.mini-chef img{width:38px;height:38px;border-radius:8px;object-fit:cover}
.mini-chef b{font-size:13.5px;font-weight:600}
.mini-chef p{font-size:10.5px;color:#96A3B1;margin-top:2px}
.dates{display:flex;gap:8px}
.date{flex:1;height:46px;border:1px solid #E1E7EE;border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:11px;color:var(--ink3);line-height:1.4;transition:.15s}
.date b{font-size:12.5px;color:#31465A;font-weight:600}
.date.on{background:var(--navy);border-color:var(--navy);color:rgba(255,255,255,.7)}
.date.on b{color:#fff}
.slots{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:9px}
.slot{height:35px;border:1px solid #E1E7EE;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:11.5px;color:#5A6B7D;transition:.15s}
.slot.on{background:#F6EFE3;border-color:#E0C79C;color:#7A5521;font-weight:600}
.slot.off{background:#F7F9FB;color:#C4CDD7;border-color:#EDF1F5;text-decoration:line-through}
.stepper{display:flex;align-items:center;gap:14px;margin-left:auto}
.stepper u{text-decoration:none;width:28px;height:28px;border:1px solid #E1E7EE;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#5A6B7D;font-size:15px}
.stepper b{font-size:15px;font-weight:600;min-width:18px;text-align:center}
.addr{display:flex;gap:10px;align-items:flex-start}
.addr .ic{color:var(--brass-t);margin-top:2px}
.addr b{font-size:13px;font-weight:600}
.addr p{font-size:11px;color:var(--ink3);margin-top:4px}
.fee{display:flex;justify-content:space-between;font-size:12px;color:#5A6B7D;padding:4px 0}
.fee.tot{border-top:1px dashed #EEF1F5;margin-top:8px;padding-top:10px;color:var(--ink);font-size:13px}
/* orders */
.tabs{display:flex;gap:19px;padding:0 20px;height:40px;align-items:center;background:#fff;border-bottom:1px solid #F0F3F7;overflow-x:auto}
.tabs::-webkit-scrollbar{display:none}
.tabs span{font-size:12.5px;color:var(--ink3);position:relative;height:40px;display:flex;align-items:center;white-space:nowrap}
.tabs span.on{color:var(--ink);font-weight:600;font-size:13.5px}
.tabs span.on:after{content:"";position:absolute;left:50%;transform:translateX(-50%);bottom:0;width:18px;height:2px;background:var(--brass2)}
.ord{background:#fff;border:1px solid var(--line);border-radius:14px;padding:13px 14px;margin-bottom:10px}
.ord .h{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.ord .h s{text-decoration:none;font-size:10px;color:var(--ink4);white-space:nowrap}
.ord .b{display:flex;gap:11px}
.ord .b img{width:62px;height:62px;border-radius:8px;object-fit:cover}
.ord .b .i{flex:1;min-width:0}
.ord .b b{font-size:13.5px;font-weight:600}
.ord .b p{font-size:10.5px;color:#96A3B1;margin-top:5px}
.alert{display:flex;align-items:center;gap:6px;background:#F7F9FB;border-radius:6px;padding:7px 10px;font-size:10.5px;color:#5A6B7D;margin-top:10px}
.alert .ic{width:13px;height:13px;color:var(--brass-t);stroke-width:2}
.ord .f{display:flex;gap:8px;justify-content:flex-end;margin-top:11px}
.ord .f .mini.g{background:#fff;color:#31465A;border:1px solid #DCE3EA}
.bar-p{height:4px;border-radius:2px;background:#EDF1F5;margin-top:8px;overflow:hidden}
.bar-p i{display:block;height:100%;background:var(--brass2);transition:width .6s}
/* order detail */
.st-hd{background:var(--navy);color:#fff;padding:calc(var(--sat) + 8px) 20px 22px;flex:0 0 auto}
.st-hd .t1{font-size:19px;font-weight:600;display:flex;align-items:center;gap:8px}
.st-hd .t2{font-size:11.5px;color:rgba(255,255,255,.62);margin-top:8px}
.tl{display:flex;gap:12px}
.tl .dotcol{width:12px;display:flex;flex-direction:column;align-items:center;padding-top:4px}
.tl .dotcol i{width:8px;height:8px;border-radius:50%;background:#DCE3EA;flex:0 0 auto}
.tl .dotcol i.on{background:var(--navy)}
.tl .dotcol i.gold{background:var(--brass2);box-shadow:0 0 0 4px #F6EFE3}
.tl .dotcol u{width:1px;flex:1;background:#EDF1F5;margin:4px 0;min-height:18px}
.tl .item{padding-bottom:10px;flex:1}
.tl .item b{font-size:12.5px;font-weight:600}
.tl .item .tm{font-size:10.5px;color:var(--ink4);margin-left:8px;font-weight:400}
.tl .item p{font-size:10.5px;color:var(--ink3);margin-top:3px}
.tl .item.fut b,.tl .item.fut p{color:#C4CDD7}
.chef-row{display:flex;align-items:center;gap:11px}
.chef-row img{width:44px;height:44px;border-radius:50%;object-fit:cover}
.chef-row b{font-size:13.5px;font-weight:600}
.chef-row p{font-size:10.5px;color:#96A3B1;margin-top:3px}
.icol{width:34px;height:34px;border-radius:50%;border:1px solid #E1E7EE;display:flex;align-items:center;justify-content:center;color:#31465A}
.icol .ic{width:17px;height:17px}
.info-row{display:flex;padding:5px 0;font-size:12px}
.info-row span{width:64px;color:#96A3B1;flex:0 0 auto}
.info-row b{font-weight:400;line-height:1.6}
/* review */
.stars{display:flex;gap:10px;justify-content:center;margin:6px 0}
.stars .ic{width:32px;height:32px;color:#C6A265;fill:#C6A265;stroke:none;transition:.15s}
.stars .ic.o{fill:none;stroke:#DCE3EA;stroke-width:1.6}
.tarea{border:1px solid var(--line);border-radius:10px;padding:11px 12px;font-size:12px;color:var(--ink);line-height:1.7;min-height:74px;position:relative;width:100%;font-family:inherit;outline:0;resize:none}
.tarea:focus{border-color:#C9D3DE}
.photos{display:flex;gap:9px}
.photos img{width:64px;height:64px;border-radius:8px;object-fit:cover}
.plus{width:64px;height:64px;border:1px dashed #D3DCE4;border-radius:8px;display:flex;align-items:center;justify-content:center;color:var(--ink4);background:#FAFBFC}
.sw-row{display:flex;align-items:center;justify-content:space-between;font-size:12px;color:#5A6B7D;padding:12px 0 2px}
.switch{width:42px;height:24px;border-radius:12px;background:var(--navy);position:relative;transition:.2s}
.switch i{position:absolute;right:3px;top:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:.2s}
.switch.off{background:#DCE3EA}
.switch.off i{right:21px}
/* stats */
.fin-hd{background:var(--navy);color:#fff;padding:calc(var(--sat) + 8px) 20px 24px;flex:0 0 auto}
.fin-hd .h1{font-size:19px;font-weight:600}
.period{font-size:11.5px;color:rgba(255,255,255,.75);border:1px solid rgba(255,255,255,.22);padding:4px 9px;border-radius:4px;display:flex;align-items:center;gap:3px}
.fstats{display:flex;margin-top:18px}
.fstats div{flex:1;border-right:1px solid rgba(255,255,255,.12)}
.fstats div:last-child{border:0}
.fstats b{font-size:22px;font-weight:600;display:block;line-height:1.2}
.fstats b i{font-style:normal;font-size:11px;font-weight:400;color:rgba(255,255,255,.6);margin-left:2px}
.fstats p{font-size:10.5px;color:rgba(255,255,255,.58);margin-top:5px}
.seg{display:flex;gap:6px;background:#fff;border:1px solid var(--line);border-radius:8px;padding:4px;margin-bottom:12px}
.seg span{flex:1;text-align:center;font-size:11.5px;padding:7px 0;border-radius:6px;color:var(--ink3);transition:.15s}
.seg span.on{background:var(--navy);color:#fff;font-weight:600}
.bars{display:flex;align-items:flex-end;gap:12px;height:132px;position:relative;padding:14px 4px 0}
.grid-l{position:absolute;left:0;right:0;top:14px;bottom:0;display:flex;flex-direction:column;justify-content:space-between}
.grid-l i{height:1px;background:#F1F4F8}
.bcol{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;position:relative;z-index:2;height:100%}
.bcol .v{font-size:9.5px;color:var(--ink3);margin-bottom:5px}
.bcol .b{width:16px;border-radius:2px 2px 0 0;background:#C9D6E1;transition:height .5s ease}
.bcol .b.on{background:var(--navy)}
.bcol .lb{font-size:10px;color:var(--ink3);margin-top:8px}
.bcol .lb.on{color:var(--ink);font-weight:600}
.pref{display:flex;align-items:center;gap:10px;padding:7px 0;font-size:11.5px;color:#5A6B7D}
.pref span{width:62px;flex:0 0 auto}
.pref .t{flex:1;height:5px;border-radius:3px;background:#EEF1F5;overflow:hidden}
.pref .t i{display:block;height:100%;background:var(--brass);transition:width .5s}
.pref b{width:32px;text-align:right;font-size:11px;color:#31465A;font-weight:600}
.rec{display:flex;align-items:center;gap:11px;padding:11px 0;border-bottom:1px solid #F0F3F7;font-size:12px}
.rec:last-child{border:0}
.rec .d{width:34px;flex:0 0 auto;text-align:center;color:var(--ink3);font-size:10px;line-height:1.3}
.rec .d b{display:block;font-size:13px;color:#31465A}
.rec .i{flex:1}
.rec .i b{font-size:12.5px;font-weight:600}
.rec .i p{font-size:10px;color:var(--ink4);margin-top:3px}
/* msgs */
.msg{display:flex;gap:11px;padding:14px 0;border-bottom:1px solid #F2F5F8}
.msg .ic-box{width:38px;height:38px;border-radius:9px;flex:0 0 auto;display:flex;align-items:center;justify-content:center}
.m1{background:#EAEFF4;color:#1B3B55}.m2{background:#FBF3E6;color:var(--brass-t)}.m3{background:#E9F2ED;color:var(--green)}
.msg .c{flex:1;min-width:0}
.msg .t{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600}
.msg .t u{text-decoration:none;width:5px;height:5px;border-radius:50%;background:#C1473C;display:block}
.msg p{font-size:11.5px;color:var(--ink3);line-height:1.7;margin-top:4px}
.msg .tm{font-size:10px;color:#B3BEC9;white-space:nowrap;margin-top:3px}
.date-sep{font-size:10.5px;color:var(--ink4);padding:14px 0 4px}
/* me */
.me-hd{background:var(--navy);color:#fff;padding:calc(var(--sat) + 4px) 20px 22px;flex:0 0 auto}
.me-row{display:flex;align-items:center;gap:14px}
.me-av{width:58px;height:58px;border-radius:50%;background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.3);display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:600;flex:0 0 auto}
.me-row .n{font-size:17px;font-weight:600;display:flex;align-items:center;gap:8px}
.lv{font-size:9.5px;background:var(--brass2);color:#16202A;padding:2px 6px;border-radius:3px;font-weight:600}
.me-row p{font-size:10.5px;color:rgba(255,255,255,.6);margin-top:5px}
.me-stats{display:flex;margin-top:18px;text-align:center}
.me-stats div{flex:1}
.me-stats b{font-size:17px;font-weight:600}
.me-stats p{font-size:10px;color:rgba(255,255,255,.58);margin-top:3px}
.qo{display:flex;padding:16px 0 14px;background:#fff;border-radius:12px;border:1px solid var(--line)}
.qo div{flex:1;display:flex;flex-direction:column;align-items:center;gap:7px;font-size:11px;color:#5A6B7D;position:relative}
.qo .ic{width:22px;height:22px;color:#31465A}
.qo b{position:absolute;top:-2px;right:50%;margin-right:-20px;font-size:9px;background:#C1473C;color:#fff;padding:1px 4px;border-radius:6px}
.menu{background:#fff;border-radius:12px;border:1px solid var(--line);margin-top:10px;padding:0 14px}
.mi{display:flex;align-items:center;gap:11px;height:49px;font-size:12.5px;color:#31465A;border-bottom:1px solid #F2F5F8}
.mi:last-child{border:0}
.mi .ic{width:19px;height:19px;color:#7B8A99}
.mi span{flex:1}
.mi em{font-style:normal;font-size:10.5px;color:var(--ink4);margin-right:4px}
.mini-chart{display:flex;align-items:flex-end;gap:3px;margin-right:4px}
.mini-chart i{width:3px;background:#C9D6E1;border-radius:1px}
.ver{text-align:center;font-size:10px;color:#B3BEC9;padding:16px 0 calc(16px + var(--sab))}
/* toast / sheet */
.toast{position:fixed;left:50%;bottom:120px;transform:translate(-50%,10px);background:rgba(20,32,44,.9);color:#fff;font-size:12.5px;padding:9px 16px;border-radius:8px;opacity:0;pointer-events:none;transition:.25s;z-index:99;max-width:80%;text-align:center;backdrop-filter:blur(6px)}
.toast.on{opacity:1;transform:translate(-50%,0)}
.mask{position:fixed;inset:0;background:rgba(10,20,30,.42);opacity:0;pointer-events:none;transition:.25s;z-index:60}
.mask.on{opacity:1;pointer-events:auto}
.sheet-b{position:fixed;left:0;right:0;bottom:0;max-width:430px;margin:0 auto;background:#fff;border-radius:18px 18px 0 0;padding:18px 20px calc(16px + var(--sab));transform:translateY(100%);transition:.28s cubic-bezier(.3,.8,.3,1);z-index:70}
.sheet-b.on{transform:none}
.sheet-b b{font-size:15px;display:block;margin-bottom:6px}
.sheet-b p{font-size:12px;color:var(--ink3);line-height:1.8}

/* 全栈版补充样式 */
body { background: #E9EDF1; }
#app { max-width: 430px; margin: 0 auto; min-height: 100dvh; background: #fff; position: relative; }
.view { display: flex; flex-direction: column; height: 100dvh; max-width: 430px; }

/* 登录验证码按钮 */
.field .code { font-size: 11.5px; color: #9C7134; font-weight: 600; padding: 4px 0 4px 10px; border-left: 1px solid #EEF1F5; }
.field .code.off { color: #A6B2BF; }

/* 骨架 / 空态 */
.empty { padding: 56px 0; text-align: center; color: #96A3B1; font-size: 12px; }

/* 消息图标盒（后端分类映射） */
.msg .c .t u { display: inline-block; }

/* 小屏适配：交互动效 */
.view > .scroll { -webkit-overflow-scrolling: touch; }

/* toast 居中可读宽度 */
.toast { max-width: 320px; text-align: center; }
</style>
</head>
<body>
<div id="app"></div>
<div class="toast" id="toast"></div>
<div class="mask" id="mask"></div>
<div class="sheet-b" id="sheet"></div>
<svg style="display:none" xmlns="http://www.w3.org/2000/svg">
<symbol id="i-home" viewBox="0 0 24 24"><path d="M3.4 11.3 12 4l8.6 7.3"/><path d="M5.6 10.2v9.4h12.8v-9.4"/><path d="M9.5 19.6v-5.3h5v5.3"/></symbol>
<symbol id="i-order" viewBox="0 0 24 24"><path d="M5.5 2.6h13v17l-2.2-1.6-2.2 1.6-2.1-1.6-2.2 1.6L10 19 7.8 20.2z"/><path d="M9 8h6M9 12h6"/></symbol>
<symbol id="i-chat" viewBox="0 0 24 24"><path d="M4 6.2A2.2 2.2 0 0 1 6.2 4h11.6A2.2 2.2 0 0 1 20 6.2v8.6A2.2 2.2 0 0 1 17.8 17H9l-4.6 3.4v-3.4z"/></symbol>
<symbol id="i-user" viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.8"/><path d="M4.8 20c.6-3.8 3.6-6 7.2-6s6.6 2.2 7.2 6"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24"><circle cx="10.8" cy="10.8" r="6"/><path d="M15.4 15.4 20 20"/></symbol>
<symbol id="i-bell" viewBox="0 0 24 24"><path d="M6 10a6 6 0 0 1 12 0c0 4 1.4 5.6 2.2 6.6H3.8C4.6 15.6 6 14 6 10z"/><path d="M9.8 20a2.4 2.4 0 0 0 4.4 0"/></symbol>
<symbol id="i-pin" viewBox="0 0 24 24"><path d="M12 21s7-6.3 7-11.2A7 7 0 0 0 5 9.8C5 14.7 12 21 12 21z"/><circle cx="12" cy="9.8" r="2.6"/></symbol>
<symbol id="i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.2"/><path d="M12 7.6V12l3 2"/></symbol>
<symbol id="i-star" viewBox="0 0 24 24"><path d="M12 3.6l2.6 5.6 6 .7-4.5 4.2 1.2 6-5.3-3-5.3 3 1.2-6L4.4 9.9l6-.7z"/></symbol>
<symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 3.5 19 6v6c0 4.6-3 7.5-7 8.5-4-1-7-3.9-7-8.5V6z"/><path d="M9 12.2l2.1 2.1 4-4.2"/></symbol>
<symbol id="i-sliders" viewBox="0 0 24 24"><path d="M4 7h9M15 7h5M4 12h3M9 12h11M4 17h11M17 17h3"/><circle cx="13" cy="7" r="2"/><circle cx="7" cy="12" r="2"/><circle cx="15.5" cy="17" r="2"/></symbol>
<symbol id="i-left" viewBox="0 0 24 24"><path d="M14.5 5.5 8 12l6.5 6.5"/></symbol>
<symbol id="i-right" viewBox="0 0 24 24"><path d="M9.5 5.5 16 12l-6.5 6.5"/></symbol>
<symbol id="i-down" viewBox="0 0 24 24"><path d="M6.5 9.5 12 15l5.5-5.5"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="M5 12.5 9.6 17 19 7"/></symbol>
<symbol id="i-phone" viewBox="0 0 24 24"><path d="M8 3.8h8a2 2 0 0 1 2 2v12.4a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5.8a2 2 0 0 1 2-2z"/><path d="M10 6.6h4"/></symbol>
<symbol id="i-users" viewBox="0 0 24 24"><circle cx="9.4" cy="8.4" r="3.2"/><path d="M3.6 19.4c.5-3.2 3-5 5.8-5s5.3 1.8 5.8 5"/><path d="M16 5.6a3 3 0 0 1 0 5.6M17.6 14.6c2 .6 3.2 2.3 3.6 4.8"/></symbol>
<symbol id="i-card" viewBox="0 0 24 24"><rect x="3.4" y="5.6" width="17.2" height="12.8" rx="2.2"/><path d="M3.4 9.8h17.2M7 15.2h3.4"/></symbol>
<symbol id="i-edit" viewBox="0 0 24 24"><path d="M16.6 4.4l3 3L8.4 18.6 4.4 20l1.4-4z"/></symbol>
<symbol id="i-gift" viewBox="0 0 24 24"><rect x="4" y="9.4" width="16" height="10.4" rx="1.6"/><path d="M3 6.6h18v3H3zM12 6.6v13.2"/><path d="M12 6.6c-1.4-3-5.6-3.4-5.6-1.2 0 1.2 2 1.2 5.6 1.2zM12 6.6c1.4-3 5.6-3.4 5.6-1.2 0 1.2-2 1.2-5.6 1.2z"/></symbol>
<symbol id="i-coin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.4"/><path d="M12 7.4v9.2M9.4 10.2h5.2M9.4 13.4h5.2"/></symbol>
<symbol id="i-leaf" viewBox="0 0 24 24"><path d="M19.6 4.4c-8.4-.6-13.6 4-13.6 10 0 2 .8 3.2 2.4 4M19.6 4.4c.6 8.4-4 13.6-10 13.6H7"/></symbol>
<symbol id="i-bowl" viewBox="0 0 24 24"><path d="M3.6 11.4h16.8c0 4.6-3.4 7.6-8.4 7.6s-8.4-3-8.4-7.6z"/><path d="M10 8.4c0-1.6 1.6-1.6 1.6-3.4M14 8.4c0-1.4 1.2-1.4 1.2-3"/></symbol>
<symbol id="i-fire" viewBox="0 0 24 24"><path d="M12 3.4c3.4 4.2 5.6 5.8 5.6 9a5.6 5.6 0 0 1-11.2 0c0-2.2 1.2-3.4 2.2-5"/><path d="M12 20.4c-1.6 0-2.6-1.2-2.6-2.6 0-1.6 1.2-2.6 2.6-4 1.4 1.4 2.6 2.4 2.6 4 0 1.4-1 2.6-2.6 2.6z"/></symbol>
<symbol id="i-heart" viewBox="0 0 24 24"><path d="M12 20.4c-1.6-1.2-8-5.4-8-9.6a4.4 4.4 0 0 1 8-2.4 4.4 4.4 0 0 1 8 2.4c0 4.2-6.4 8.4-8 9.6z"/></symbol>
<symbol id="i-book" viewBox="0 0 24 24"><path d="M4.4 5.4a2 2 0 0 1 2-2H20v15H6.4a2 2 0 0 0-2 2z"/><path d="M8.4 8.4h7M8.4 12h5"/></symbol>
<symbol id="i-camera" viewBox="0 0 24 24"><rect x="3.4" y="7.4" width="17.2" height="12" rx="2.2"/><circle cx="12" cy="13.4" r="3.2"/><path d="M8.6 7.4l1-2h4.8l1 2"/></symbol>
<symbol id="i-share" viewBox="0 0 24 24"><circle cx="17.6" cy="6" r="2.8"/><circle cx="6.4" cy="12" r="2.8"/><circle cx="17.6" cy="18" r="2.8"/><path d="M9 10.6 15 7.4M9 13.4l6 3.2"/></symbol>
<symbol id="i-spark" viewBox="0 0 24 24"><path d="M12 3.6l1.8 5.4 5.4 1.8-5.4 1.8-1.8 5.4-1.8-5.4L6.6 10.8l5.4-1.8z"/></symbol>
<symbol id="i-filter" viewBox="0 0 24 24"><path d="M4 6h16M7 12h10M10 18h4"/></symbol>
<symbol id="i-msg" viewBox="0 0 24 24"><path d="M4 6.4A2.4 2.4 0 0 1 6.4 4h11.2A2.4 2.4 0 0 1 20 6.4v7.2A2.4 2.4 0 0 1 17.6 16H12l-4 3.2v-3.2H6.4A2.4 2.4 0 0 1 4 13.6z"/></symbol>
</svg>
<script>/* 主厨到家 · 前端（对接 /api） */
const $ = (s) => document.querySelector(s);
const g = (id, cls = 'ic') => `<svg class="${cls}"><use href="#${id}"/></svg>`;
const money = (n) => '¥' + Number(n || 0).toLocaleString('zh-CN');
const pad2 = (n) => String(n).padStart(2, '0');

const S = {
  view: 'splash', stack: [], token: localStorage.getItem('zc_token') || '',
  user: null, home: null, cat: '家常三餐',
  chefId: 3, chef: null, pkgId: null,
  dates: [], date: '', slots: [],
  people: 4, tastes: ['少油少盐'], note: '',
  orders: [], counts: {}, orderTab: 'pending',
  orderId: '', order: null,
  rating: 5, reviewTags: [], reviewText: '', anonymous: true,
  stats: null, range: '6m', messages: null, msgTab: 'all',
  me: null, unread: 0,
};

/* ---------------- 网络 ---------------- */
async function api(path, opt = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opt.headers || {});
  if (S.token) headers['Authorization'] = 'Bearer ' + S.token;
  const res = await fetch('/api' + path, Object.assign({}, opt, { headers }));
  let data = null;
  try { data = await res.json(); } catch (e) { }
  if (res.status === 401) {
    S.token = ''; localStorage.removeItem('zc_token');
    if (S.view !== 'login') { S.view = 'login'; S.stack = []; render(); }
    throw new Error((data && data.detail) || '请先登录');
  }
  if (!res.ok) throw new Error((data && data.detail) || '请求失败');
  return data;
}

/* ---------------- 通用片段 ---------------- */
const STATUS_PILL = { pending: 'nv', serving: 'br', to_review: 'rd', done: 'gr', canceled: '' };
const MSG_ICON = { order: ['i-order', 'm1'], promo: ['i-gift', 'm3'], system: ['i-star', 'm1'], service: ['i-shield', 'm3'] };
const CAT_ICON = ['i-bowl', 'i-users', 'i-heart', 'i-leaf'];
const TASTES = ['少油少盐', '忌辣', '软烂', '清淡'];
const REVIEW_TAGS = ['手艺好', '准时到达', '厨房收拾干净', '口味地道', '沟通顺畅', '分量足'];

function tabbar(cur) {
  const t = [['home', 'i-home', '首页'], ['orders', 'i-order', '订单'], ['msgs', 'i-chat', '消息'], ['me', 'i-user', '我的']];
  return `<div class="tabbar">${t.map(([v, ic, n]) => `<div class="${cur === v ? 'on' : ''}" data-act="tab:${v}">${g(ic)}${n}</div>`).join('')}</div>`;
}
function loading(text = '加载中') {
  return `<div style="padding:60px 0;text-align:center;color:#8B99A8;font-size:12px">${text}…</div>`;
}

/* ---------------- 视图 ---------------- */
const V = {};

V.splash = () => `
<div class="view" data-v="splash"><div class="splash">
  <img src="https://media.doubao.com/space/api/box/stream/download/all_by_mount_point/K7Rnbwl8XowMqRx9lkRcx2l6n5f" alt="">
  <div class="veil"></div>
  <div class="sp-body">
    <div class="mark">厨</div><h1>主厨到家</h1><p class="en">PRIVATE CHEF AT HOME</p>
    <div class="rule"></div><p class="tagline">一桌好菜，等你回家</p>
    <div class="enter" data-act="enter">开始体验 ${g('i-right', 'ic s')}</div>
  </div>
  <div class="sp-foot">实名认证 · 健康证上岗 · 全程意外险<br>v1.0.0 · 在线版</div>
</div></div>`;

V.login = () => `
<div class="view" data-v="login">
  <div class="topbar"><span data-act="toast:客服 400-880-1234">${g('i-left')}</span><span class="sp"></span><span class="sub">需要帮助</span></div>
  <div class="scroll pad">
    <h1 class="h-xl" style="margin-top:10px">欢迎回家</h1>
    <p class="sub" style="margin-top:8px">登录后即可预约私厨上门，查看上门进度</p>
    <div style="margin-top:28px">
      <div class="field"><span class="pre">+86</span><span class="sep"></span>
        <input id="phone" inputmode="numeric" maxlength="11" placeholder="请输入手机号" value="${S.phone || ''}"></div>
      <div class="field"><input id="code" inputmode="numeric" maxlength="6" placeholder="6 位验证码" value="${S.codeValue || ''}">
        <span class="code ${S.codeCooling ? 'off' : ''}" data-act="code">${S.codeCooling ? `重新获取(${S.codeCooling}s)` : '获取验证码'}</span></div>
    </div>
    <div class="agree"><span class="box on">✓</span><span>已阅读并同意 <span style="color:#7A5521">《用户服务协议》</span> 与 <span style="color:#7A5521">《隐私政策》</span></span></div>
    <div style="height:24px"></div>
    <button class="btn-p" data-act="login">登录 / 注册</button>
    <div class="divider"><i></i>新用户直接注册<i></i></div>
    <div style="border-top:1px solid #F0F3F7;margin-top:30px;padding-top:20px">
      <p style="font-size:11px;letter-spacing:.16em;color:#B98B52;font-weight:600">SERVICE PROMISE</p>
      <div style="display:flex;margin-top:16px;text-align:center">
        <div style="flex:1">${g('i-shield')}<p style="font-size:10.5px;color:#8B99A8;margin-top:8px">实名厨师</p></div>
        <div style="flex:1">${g('i-bowl')}<p style="font-size:10.5px;color:#8B99A8;margin-top:8px">含餐后清洁</p></div>
        <div style="flex:1">${g('i-check')}<p style="font-size:10.5px;color:#8B99A8;margin-top:8px">不满意重做</p></div>
      </div>
    </div>
    <div class="gift" style="margin-top:28px">${g('i-gift')}<div><b>新人礼 · 首单立减 30 元</b><p>注册即送，有效期 7 天</p></div></div>
    <div style="text-align:center;font-size:11px;color:var(--ink4);padding:18px 0 calc(18px + var(--sab))">客服热线 400-880-1234</div>
  </div>
</div>`;

V.home = () => {
  if (!S.home) return `<div class="view">${loading()}</div>`;
  const chefs = S.home.chefs, cat = S.cat;
  const hot = (S.home.packages || []).filter(p => cat === '全部' || true).slice(0, 3);
  return `<div class="view" data-v="home"><div class="scroll" style="background:#F5F7F9">
  <div class="home-hd">
    <div class="loc">${g('i-pin', 'ic s')}郑州 · 金水区 ${g('i-down', 'ic xs')}
      <span class="bell" data-act="tab:msgs">${g('i-bell', 'ic')}${S.unread ? '<span class="bdot"></span>' : ''}</span></div>
    <div class="searchbar" data-act="toast:搜索功能可接搜索服务">${g('i-search', 'ic s')}搜「红烧肉」「六人家宴」<span class="sbtn">搜索</span></div>
    <div class="cats">${S.home.categories.map((c, i) => `<div class="cat ${c.key === cat ? 'on' : ''}" data-act="cat:${c.key}"><i>${g(CAT_ICON[i])}</i>${c.key}</div>`).join('')}</div>
  </div>
  <div class="pad" style="background:#F5F7F9;padding-bottom:14px">
    <div class="hero-b" data-act="go:list"><img src="${S.home.banner.image}" alt=""><div class="hv"></div>
      <div class="txt"><em>SEASONAL · 家宴定制</em><b>${S.home.banner.title} 主厨上门</b><p>${S.home.banner.sub}</p><u>立即预约 ¥298 起</u></div></div>
    <div class="sec-head" style="margin-top:16px"><b>今晚有空的名厨</b><span class="more" data-act="go:list">查看全部 ${g('i-right', 'ic xs')}</span></div>
    <div class="chef-b">${chefs.slice(0, 2).map(x => `<div class="cb" data-act="chef:${x.id}"><img src="${x.avatar}" alt="">
      <div><div class="n">${x.name}</div><div class="m">${x.cuisine}</div><div class="st">${x.rating.toFixed(1)}<s>·${x.distance}km</s></div></div></div>`).join('')}</div>
    <div class="sec-head"><b>本周热门套餐</b><span class="more" data-act="go:list">更多套餐 ${g('i-right', 'ic xs')}</span></div>
    ${hot.map(p => `<div class="pkg" data-act="chef:${p.chefId}"><img src="${p.cover}" alt="">
      <div><b>${p.name}</b><p>${p.desc}</p></div><span class="px">¥${p.price}<i>起</i></span></div>`).join('')}
    <div class="card" style="margin-top:14px;padding:12px 14px;display:flex;text-align:center">
      ${[[S.home.stats.chefs, '认证厨师'], [S.home.stats.orders, '累计服务'], [S.home.stats.rating.toFixed(2), '平均评分']].map(([v, l]) =>
        `<div style="flex:1"><b style="font-size:17px;color:#12293D">${v}</b><p style="font-size:10.5px;color:#8B99A8;margin-top:2px">${l}</p></div>`).join('')}
    </div>
  </div></div>${tabbar('home')}</div>`;
};

V.list = () => {
  if (!S.home) return `<div class="view">${loading()}</div>`;
  let items = S.home.chefs.filter(c => c.categories.includes(S.cat));
  const by = S.sortBy || 'rating';
  items.sort((a, b) => by === 'distance' ? a.distance - b.distance : by === 'price' ? a.basePrice - b.basePrice : b.rating - a.rating);
  return `<div class="view" data-v="list">
  <div class="topbar" style="border-bottom:1px solid #F5F7F9"><span data-act="back">${g('i-left', 'ic')}</span><b>${S.cat}</b><span class="sp"></span><span data-act="go:home">${g('i-search')}</span></div>
  <div class="filterbar">
    ${[['rating', '综合排序'], ['distance', '距离最近'], ['price', '价格最低']].map(([k, n]) =>
      `<span class="sort ${by === k ? 'on' : ''}" data-act="sort:${k}">${n}</span>`).join('')}
    <span class="fl" data-act="toast:可在下一步接入多条件筛选">${g('i-filter', 'ic s')}</span>
  </div>
  <div class="fchips"><span class="chip line on">菜系 · 全部</span><span class="chip line" data-act="toast:口味筛选待接入">口味 · 不限</span>
    <span class="chip line" data-act="toast:已按可约时段过滤">今日可约</span><span class="chip line" data-act="toast:已过滤含食材">含食材</span></div>
  <div class="scroll pad" style="background:#F5F7F9;padding-top:2px">
    <div class="hint">金水区 · ${items.length} 位厨师可预约</div>
    ${items.map(x => `<div class="chef-c" data-act="chef:${x.id}"><img src="${x.avatar}" alt="">
      <div style="flex:1;min-width:0">
        <div class="b1"><b>${x.name}</b><span class="pill ${x.id === 2 ? 'br' : 'nv'}">${x.cuisine}</span></div>
        <div class="meta"><s>${x.rating.toFixed(1)}</s> 分 · ${x.orders} 单 · ${x.distance}km</div>
        <div class="tg">${x.tags.map(t => `<em>${t}</em>`).join('')}</div>
        <div class="f1"><span class="px">¥${x.basePrice}<i>/次起</i></span>
          <span class="dotok"><i></i>今日可约</span><span class="mini">预约</span></div>
      </div></div>`).join('')}
    <div style="height:calc(10px + var(--sab))"></div>
  </div></div>`;
};

V.detail = () => {
  const x = S.chef;
  if (!x) return `<div class="view">${loading('厨师资料加载中')}</div>`;
  return `<div class="view" data-v="detail" style="position:relative">
  <div class="hero"><img src="${x.hero}" alt=""><div class="hv"></div>
    <div class="flt"><span class="circ" data-act="back">${g('i-left', 'ic s')}</span><span class="circ" data-act="toast:已复制主页链接">${g('i-share', 'ic s')}</span></div></div>
  <div class="sheet"><div class="scroll" style="margin:0 -20px;padding:0 20px">
    <div class="sh-head"><img src="${x.avatar}" alt="">
      <div style="flex:1;padding-top:2px">
        <div class="n1">${x.name} <span class="pill br" style="height:18px;font-size:9.5px">${x.badges[0]}</span></div>
        <div class="badges">${x.badges.slice(1).map(b => `<span class="pill">${b}</span>`).join('')}</div>
        <div class="stat"><span><s>${x.rating.toFixed(1)}</s> 分</span><span><b>${x.orders}</b> 单</span><span>${x.distance}km · 金水区</span></div>
      </div></div>
    <hr class="hr">
    <div class="sec-head" style="margin:0 0 6px"><b>${x.cuisine}</b><span class="sub">从业 ${8 + x.id} 年</span></div>
    <div style="font-size:12px;color:#5A6B7D;line-height:1.7">${x.bio}</div>
    <div class="tg" style="margin-top:10px">${x.tags.map(t => `<span class="chip">${t}</span>`).join('')}</div>
    <hr class="hr">
    <div class="sec-head" style="margin:0 0 2px"><b>可选套餐</b><span class="sub">${x.packages.length} 个</span></div>
    ${x.packages.map(p => `<div class="pk-row ${p.id === S.pkgId ? 'on' : ''}" data-act="pkg:${p.id}">
      <div class="left"><b>${p.name}</b><p>${p.desc} · ${p.minutes} 分钟 · 食材 ¥${p.foodCost}</p></div>
      <span class="px">¥${p.price}</span></div>`).join('')}
    <hr class="hr">
    <div class="sec-head" style="margin:0 0 2px"><b>用户评价 <span style="font-size:11px;color:#96A3B1;font-weight:400">${x.reviews.length} 条</span></b></div>
    ${x.reviews.map(r => `<div class="rv"><span class="a">${r.anonymous ? '匿' : r.user[0]}</span><div class="c">
      <div class="t"><b>${r.user}</b><span style="color:#C6A265">${'★'.repeat(Math.round(r.rating))}</span><span>${(r.at || '').slice(5, 10) || '近期'}</span></div>
      <p>${r.content}</p>
      ${r.tags && r.tags.length ? `<div class="tg" style="margin-top:6px">${r.tags.map(t => `<em>${t}</em>`).join('')}</div>` : ''}
    </div></div>`).join('')}
    <div style="background:#F7F9FB;border-radius:8px;padding:10px 12px;margin:12px 0 6px;display:flex;gap:14px;justify-content:space-between;font-size:10px;color:var(--ink3)">
      <span style="display:flex;gap:4px;align-items:center">${g('i-shield', 'ic xs')}实名认证</span>
      <span style="display:flex;gap:4px;align-items:center">${g('i-check', 'ic xs')}健康证</span>
      <span style="display:flex;gap:4px;align-items:center">${g('i-coin', 'ic xs')}先做后付</span>
    </div><div style="height:12px"></div>
  </div>
  <div class="cta" style="margin:0 -20px;padding-left:20px;padding-right:20px">
    <div style="flex:1"><div style="font-size:10.5px;color:#96A3B1">套餐起价</div>
      <div class="px" style="font-size:20px">¥${x.packages[0].price}<i>/次</i></div></div>
    <button class="btn-p" style="width:150px;height:46px" data-act="go:booking">立即预约</button>
  </div></div></div>`;
};

V.booking = () => {
  const x = S.chef;
  if (!x) return `<div class="view">${loading()}</div>`;
  const p = x.packages.find(q => q.id === S.pkgId) || x.packages[0];
  const total = p.price + p.foodCost + 9;
  return `<div class="view" data-v="booking">
  <div class="topbar"><span data-act="back">${g('i-left', 'ic')}</span><b>确认预约</b><span class="sp"></span><span class="sub" data-act="toast:上门前 2 小时可免费取消">规则</span></div>
  <div class="scroll bk" style="background:#F5F7F9">
    <div class="mini-chef"><img src="${x.avatar}" alt=""><div><b>${x.name} · ${p.name.split(' · ')[0]}</b>
      <p>${x.cuisine} · ${x.rating.toFixed(1)} 分 · 已服务 ${x.orders} 单</p></div><span class="px" style="margin-left:auto">¥${p.price}</span></div>
    <div class="blk"><div class="hd"><b>服务时间</b><span>北京时间 · 今日起可约</span></div>
      <div class="dates">${S.dates.map(d => `<div class="date ${d.date === S.date ? 'on' : ''}" data-act="date:${d.date}"><b>${d.day}</b>${d.mmdd}</div>`).join('')}</div>
      <div class="slots">${(S.slots || []).map(s => `<div class="slot ${s.disabled ? 'off' : ''} ${s.slot === S.slot && !s.disabled ? 'on' : ''}"
        data-act="${s.disabled ? 'toast:' + (s.reason || '该时段已约满') : 'slot:' + s.slot}">${s.slot}</div>`).join('')}</div>
    </div>
    <div class="blk" style="padding:12px 14px"><div style="display:flex;align-items:center"><b style="font-size:13.5px;font-weight:600">用餐人数</b>
      <div class="stepper"><u data-act="people:-1">−</u><b>${S.people}</b><u data-act="people:1">+</u></div></div></div>
    <div class="blk"><div class="hd" style="margin-bottom:10px"><b>口味偏好</b><span>可多选，厨师会照着做</span></div>
      <div style="display:flex;gap:7px;flex-wrap:wrap">${TASTES.map(t => `<span class="chip ${S.tastes.includes(t) ? 'on' : ''}" data-act="taste:${t}">${t}</span>`).join('')}</div></div>
    <div class="blk" style="padding:13px 14px"><div class="addr">${g('i-pin', 'ic s')}
      <div style="flex:1"><b>${S.user ? S.user.nickname : '张晓宁'} · 郑州市金水区</b><p>农业路 12 号 绿地老街 3 号楼 2 单元 1802</p></div>
      <span style="font-size:11px;color:#5A6B7D;border-left:1px solid #EEF1F5;padding-left:12px" data-act="toast:可在设置中维护常用地址">修改</span></div></div>
    <div class="blk"><div class="hd" style="margin-bottom:6px"><b>费用明细</b></div>
      <div class="fee"><span>上门服务费（${p.name.split(' · ')[0]}）</span><span>¥${p.price}</span></div>
      <div class="fee"><span>食材代购（预估，多退少补）</span><span>¥${p.foodCost}</span></div>
      <div class="fee"><span>平台保障服务费</span><span>¥9</span></div>
      <div class="fee tot"><span>合计</span><span class="px">¥${total}</span></div></div>
    <div style="height:8px"></div>
  </div>
  <div class="cta"><div style="flex:1"><div style="font-size:10.5px;color:#96A3B1">合计</div>
    <span class="px" style="font-size:20px">¥${total}</span></div>
    <button class="btn-p" style="width:176px;height:46px" data-act="submit">提交预约</button></div></div>`;
};

V.orders = () => {
  const tabs = [['pending', '待上门'], ['serving', '服务中'], ['to_review', '待评价'], ['done', '已完成'], ['all', '全部']];
  const list = S.orders.filter(o => S.orderTab === 'all' ? true : (S.orderTab === 'to_review' ? (o.status === 'to_review' || (o.status === 'done' && !o.reviewed)) : o.status === S.orderTab));
  return `<div class="view" data-v="orders">
  <div class="topbar" style="padding-bottom:6px"><b>我的订单</b><span class="sp"></span><span data-act="toast:客服在线 09:00-21:00">${g('i-msg')}</span></div>
  <div class="tabs">${tabs.map(([k, n]) => `<span class="${S.orderTab === k ? 'on' : ''}" data-act="otab:${k}">${n}${S.counts[k] ? ' ' + S.counts[k] : ''}</span>`).join('')}</div>
  <div class="scroll pad" style="background:#F5F7F9;padding-top:12px">
    ${list.length ? list.map(o => `<div class="ord" data-act="order:${o.id}">
      <div class="h"><span class="pill ${STATUS_PILL[o.status] || ''}">${o.statusText}</span><s>订单 ${o.id}</s></div>
      <div class="b"><img src="${o.package.cover}" alt="">
        <div class="i"><b>${o.package.name}</b><p>${o.chef.name} · ${o.chef.cuisine} · ${o.date.slice(5).replace('-', '月')}日 ${o.slot}</p>
          <p>${o.people} 人 · ${o.address.replace('郑州市', '').slice(0, 16)}</p></div>
        <span class="px">¥${o.total}</span></div>
      ${o.status === 'pending' ? `<div class="alert">${g('i-clock')}已通知厨师接单，请提前备好厨房</div>` : ''}
      ${o.status === 'serving' ? `<div class="alert" style="background:#FBF6EE;color:#7A5521">${g('i-fire')}厨师正在上门服务，约 ${o.package.minutes} 分钟</div><div class="bar-p"><i style="width:68%"></i></div>` : ''}
      <div class="f"><span class="mini g" data-act="toast:已转接厨师消息">联系厨师</span>
        <span class="mini" data-act="${o.status === 'done' && !o.reviewed || o.status === 'to_review' ? 'review:' + o.id : 'order:' + o.id}">${(o.status === 'done' && !o.reviewed) || o.status === 'to_review' ? '去评价得 20 积分' : '查看进度'}</span></div>
    </div>`).join('') : `<div style="padding:60px 0;text-align:center;color:#96A3B1;font-size:12px">这里还没有订单<br><span style="color:#9C7134" data-act="go:home">去看看名厨 →</span></div>`}
    <div style="height:calc(10px + var(--sab))"></div>
  </div>${tabbar('orders')}</div>`;
};

V.order = () => {
  const o = S.order;
  if (!o) return `<div class="view">${loading('订单加载中')}</div>`;
  const canCancel = o.status === 'pending' || o.status === 'serving';
  return `<div class="view" data-v="order">
  <div class="st-hd">
    <div style="display:flex;align-items:center;height:38px"><span data-act="back">${g('i-left', 'ic')}</span><span style="flex:1"></span><span data-act="toast:客服 400-880-1234">${g('i-phone', 'ic')}</span></div>
    <div class="t1">${o.statusText} ${g('i-clock', 'ic s')}</div>
    <div class="t2">${o.date} ${o.slot} 上门 · 订单 ${o.id}</div>
  </div>
  <div class="scroll bk" style="background:#F5F7F9;padding-top:12px">
    <div class="blk"><div class="hd"><b>服务进度</b><span style="color:#9C7134">实时更新</span></div>
      ${o.events.map((e, i) => `<div class="tl"><div class="dotcol"><i class="${e.done ? 'on' : (i === o.events.findIndex(x => !x.done) ? 'gold' : '')}"></i><u></u></div>
        <div class="item ${e.done ? '' : 'fut'}"><b>${e.title}<span class="tm">${e.at}</span></b><p>${e.detail}</p></div></div>`).join('')}
    </div>
    <div class="blk" style="padding:12px 14px"><div class="chef-row"><img src="${o.chef.avatar}" alt="">
      <div><b>${o.chef.name} · ${o.chef.cuisine}</b><p>${o.chef.rating.toFixed(1)} 分 · ${o.chef.orders} 单 · 健康证核验</p></div>
      <div style="margin-left:auto;display:flex;gap:6px"><span class="icol" data-act="toast:已发送消息给厨师">${g('i-msg', 'ic')}</span>
        <span class="icol" data-act="toast:已拨打 138****6688（示意）">${g('i-phone', 'ic')}</span></div></div></div>
    <div class="blk"><div class="hd"><b>服务信息</b><span data-act="toast:改期请提前 2 小时联系客服">修改时间</span></div>
      <div class="info-row"><span>套餐</span><b>${o.package.name}</b></div>
      <div class="info-row"><span>时间</span><b>${o.date} ${o.slot}</b></div>
      <div class="info-row"><span>地址</span><b>${o.address}</b></div>
      <div class="info-row"><span>口味</span><b>${o.tastes.length ? o.tastes.join(' · ') : '不限'}</b></div>
      <div class="info-row"><span>人数</span><b>${o.people} 人</b></div>
      ${o.note ? `<div class="info-row"><span>备注</span><b>${o.note}</b></div>` : ''}
      <div class="info-row"><span>支付</span><b>${o.status === 'canceled' ? '已退款' : '已支付'} <span class="px">¥${o.total}</span>（服务费 ${o.serviceFee} + 食材 ${o.foodFee}）</b></div></div>
    <div style="height:10px"></div>
  </div>
  <div class="cta">${canCancel ? `<button class="btn-g" style="flex:0 0 126px" data-act="cancel">取消预约</button>` :
      (o.status === 'done' && !o.reviewed || o.status === 'to_review' ? `<button class="btn-g" style="flex:0 0 126px" data-act="review:${o.id}">去评价</button>` : '')}
    ${o.status === 'canceled'
      ? `<button class="btn-p" style="flex:1;height:46px;font-size:14.5px" data-act="back">返回订单列表</button>`
      : `<button class="btn-p" style="flex:1;height:46px;font-size:14.5px" data-act="toast:已转接平台客服">联系平台客服</button>`}</div></div>`;
};

V.review = () => {
  const o = S.order;
  if (!o) return `<div class="view">${loading()}</div>`;
  return `<div class="view" data-v="review">
  <div class="topbar"><span data-act="back">${g('i-left', 'ic')}</span><b>评价服务</b><span class="sp"></span><span class="sub" data-act="tab:orders">稍后</span></div>
  <div class="scroll pad" style="padding-top:4px">
    <div style="display:flex;gap:11px;padding:6px 0 16px;border-bottom:1px solid #F0F3F7;align-items:center">
      <img src="${o.chef.avatar}" alt="" style="width:52px;height:52px;border-radius:12px;object-fit:cover">
      <div style="flex:1"><b style="font-size:15px">${o.chef.name} · ${o.chef.cuisine}</b>
        <div class="sub" style="font-size:11px;margin-top:4px">${o.date} · ${o.package.name} · ${o.people} 人</div></div>
      <span class="pill ${STATUS_PILL[o.status]}">${o.statusText}</span></div>
    <div class="sub" style="text-align:center;padding:20px 0 4px">这次上门的体验如何？</div>
    <div class="stars">${[1, 2, 3, 4, 5].map(i => `<span data-act="star:${i}">${g('i-star', 'ic ' + (i <= S.rating ? '' : 'o'))}</span>`).join('')}</div>
    <div style="text-align:center;font-size:13px;color:#9C7134;font-weight:600;letter-spacing:.06em">${['', '不满意', '一般', '还行', '满意', '非常满意'][S.rating]}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:18px">
      ${REVIEW_TAGS.map(t => `<span class="chip ${S.reviewTags.includes(t) ? 'on' : ''}" data-act="rt:${t}">${t}</span>`).join('')}</div>
    <div class="sec-head" style="margin:18px 0 8px"><b>说说具体感受</b><span style="font-size:10.5px;color:#A6B2BF">选填</span></div>
    <textarea class="tarea" id="rtext" placeholder="红烧肉很入味，厨房也收拾得很干净，老人吃得很开心……">${S.reviewText || ''}</textarea>
    <div class="sw-row"><span>匿名评价（不显示头像与昵称）</span><span class="switch ${S.anonymous ? '' : 'off'}" data-act="anon"><i></i></span></div>
    <div style="background:#F7F9FB;border-radius:8px;padding:10px 12px;font-size:10.5px;color:var(--ink3);line-height:1.7;margin-top:14px">评价成功后自动发放 20 积分，可抵 2 元服务费；文字评价将公开展示。</div>
    <div style="height:16px"></div>
  </div>
  <div class="cta"><button class="btn-p" data-act="revsubmit">提交评价</button></div></div>`;
};

V.stats = () => {
  if (!S.stats) return `<div class="view">${loading('账本统计中')}</div>`;
  const st = S.stats;
  const bars = st.bars || [];
  return `<div class="view" data-v="stats">
  <div class="fin-hd">
    <div style="display:flex;align-items:center;height:38px"><span data-act="back">${g('i-left', 'ic')}</span>
      <div class="h1" style="margin-left:2px;flex:1">我的厨房账本</div>
      <span class="period" data-act="toast:数据来自真实订单">${new Date().getFullYear()} 年</span></div>
    <div class="fstats">
      <div><b>${st.totalOrders}<i>次</i></b><p>累计预约</p></div>
      <div><b>¥${Number(st.totalSpend).toLocaleString('zh-CN')}</b><p>累计花费</p></div>
      <div><b>${st.hours}<i>h</i></b><p>服务时长</p></div></div>
  </div>
  <div class="scroll pad" style="background:#F5F7F9;padding-top:14px">
    <div class="seg"><span class="${S.range === '6m' ? 'on' : ''}" data-act="range:6m">近 6 个月</span>
      <span class="${S.range === '1m' ? 'on' : ''}" data-act="range:1m">近 1 个月</span>
      <span class="${S.range === 'y' ? 'on' : ''}" data-act="range:y">全年</span></div>
    <div class="card" style="padding-bottom:16px"><div class="sec-head" style="margin:0 0 2px"><b style="font-size:13.5px">上门服务支出</b>
      <span style="font-size:10.5px;color:#96A3B1">含食材代购</span></div>
      <div class="bars"><div class="grid-l"><i></i><i></i><i></i><i></i></div>
        ${bars.map((b, i) => `<div class="bcol"><span class="v">${b.value ? b.value : ''}</span>
          <span class="b ${i === bars.length - 1 ? 'on' : ''}" style="height:${Math.max(4, Math.round(b.pct * 0.96))}px"></span>
          <span class="lb ${i === bars.length - 1 ? 'on' : ''}">${b.label}</span></div>`).join('')}</div>
    </div>
    <div class="card" style="margin-top:10px"><div class="sec-head" style="margin:0 0 6px"><b style="font-size:13.5px">菜系偏好</b>
      <span style="font-size:10.5px;color:#96A3B1">按订单次数</span></div>
      ${st.preference.length ? st.preference.map(p => `<div class="pref"><span>${p.name}</span><span class="t"><i style="width:${p.pct}%"></i></span><b>${p.pct}%</b></div>`).join('') :
        `<div style="font-size:11px;color:#96A3B1;padding:8px 0">暂无数据</div>`}
    </div>
    <div class="card" style="margin-top:10px;padding:6px 14px 8px"><div class="sec-head" style="margin:8px 0 2px"><b style="font-size:13.5px">最近记录</b>
      <span class="more" data-act="tab:orders">全部 ${g('i-right', 'ic xs')}</span></div>
      ${S.orders.slice(0, 3).map(o => `<div class="rec"><div class="d"><b>${o.date.slice(8, 10)}</b>${o.date.slice(5, 7)}月</div>
        <div class="i"><b>${o.package.name} · ${o.chef.name}</b><p>${o.statusText} · ${o.slot.split('-')[0]}</p></div>
        <span class="px">¥${o.total}</span></div>`).join('') || '<div style="font-size:11px;color:#96A3B1;padding:8px 0">暂无记录</div>'}
    </div>
    <div style="height:calc(14px + var(--sab))"></div>
  </div></div>`;
};

V.msgs = () => {
  const m = S.messages;
  const tabs = [['all', '全部'], ['order', '订单'], ['promo', '优惠'], ['system', '系统']];
  if (!m) return `<div class="view">${loading()}</div>`;
  const items = m.items.filter(x => S.msgTab === 'all' ? true : (S.msgTab === 'system' ? (x.category === 'system' || x.category === 'service') : x.category === S.msgTab));
  return `<div class="view" data-v="msgs">
  <div class="topbar" style="padding-bottom:6px"><b>消息</b><span class="sp"></span>
    <span class="sub" data-act="readall">${m.unread ? '全部已读' : '已全部读完'}</span></div>
  <div class="tabs">${tabs.map(([k, n]) => `<span class="${S.msgTab === k ? 'on' : ''}" data-act="mtab:${k}">${n}${(k === 'system' ? ((m.counts.system || 0) + (m.counts.service || 0)) : (m.counts[k] || 0)) ? ' ' + (k === 'system' ? ((m.counts.system || 0) + (m.counts.service || 0)) : m.counts[k]) : ''}</span>`).join('')}</div>
  <div class="scroll pad">
    ${items.length ? items.map((x, i) => {
      const [ic, cls] = MSG_ICON[x.category] || MSG_ICON.system;
      const prev = items[i - 1];
      const sep = !prev || prev.at.slice(0, 10) !== x.at.slice(0, 10) ? `<div class="date-sep">${x.at.slice(5, 10).replace('-', '/')}</div>` : '';
      return sep + `<div class="msg" data-act="toast:${x.title}"><div class="ic-box ${cls}">${g(ic)}</div>
        <div class="c"><div class="t">${x.read ? '' : '<u></u>'}${x.title}</div><p>${x.body}</p></div>
        <span class="tm">${x.at.slice(11, 16)}</span></div>`;
    }).join('') : `<div style="padding:60px 0;text-align:center;color:#96A3B1;font-size:12px">暂无消息</div>`}
    <div style="height:calc(14px + var(--sab))"></div>
  </div>${tabbar('msgs')}</div>`;
};

V.me = () => {
  const u = S.user || { nickname: '张晓宁', level: 3, points: 0 };
  const st = S.stats || { totalOrders: 0, hours: 0 };
  return `<div class="view" data-v="me"><div class="scroll">
  <div class="me-hd">
    <div style="display:flex;align-items:center;height:36px"><span style="flex:1"></span><span data-act="toast:设置：通知 / 隐私 / 关于">${g('i-sliders')}</span></div>
    <div class="me-row"><div class="me-av">${u.nickname[0]}</div>
      <div style="flex:1"><div class="n">${u.nickname} <span class="lv">Lv.${u.level} 家宴常客</span></div>
        <p>累计预约 ${st.totalOrders} 单 · 厨师服务 ${st.hours} 小时</p></div>${g('i-right')}</div>
    <div class="me-stats"><div><b>${st.totalOrders}</b><p>累计订单</p></div><div><b>3</b><p>收藏厨师</p></div>
      <div><b>2</b><p>优惠券</p></div><div><b>${u.points}</b><p>积分</p></div></div>
  </div>
  <div class="pad" style="background:#F5F7F9;padding:12px 18px 0">
    <div class="qo"><div data-act="otabgo:pending"><b>${S.counts.pending || ''}</b>${g('i-order')}待上门</div>
      <div data-act="otabgo:serving">${g('i-clock')}服务中</div>
      <div data-act="otabgo:to_review"><b>${S.counts.to_review || ''}</b>${g('i-edit')}待评价</div>
      <div data-act="otabgo:all">${g('i-card')}全部订单</div></div>
    <div class="menu">
      <div class="mi" data-act="go:stats">${g('i-book')}<span>我的厨房账本</span>
        <span class="mini-chart"><i style="height:6px"></i><i style="height:10px"></i><i style="height:7px"></i><i style="height:13px"></i><i style="height:9px"></i></span>${g('i-right', 'ic xs')}</div>
      <div class="mi" data-act="toast:常用地址 2 个：家 / 公司">${g('i-pin')}<span>常用地址</span><em>2 个</em>${g('i-right', 'ic xs')}</div>
      <div class="mi" data-act="toast:家庭成员 4 人 · 已记录口味偏好">${g('i-users')}<span>家庭成员与口味偏好</span><em>4 人</em>${g('i-right', 'ic xs')}</div>
      <div class="mi" data-act="toast:优惠券 2 张 · 积分 ${u.points}">${g('i-gift')}<span>优惠券与积分</span><em>2 张</em>${g('i-right', 'ic xs')}</div>
    </div>
    <div class="menu">
      <div class="mi" data-act="toast:在线客服 09:00-21:00">${g('i-msg')}<span>在线客服</span><em>09:00-21:00</em>${g('i-right', 'ic xs')}</div>
      <div class="mi" data-act="toast:食品安全与服务保障说明">${g('i-shield')}<span>食品安全与服务保障</span>${g('i-right', 'ic xs')}</div>
      <div class="mi" data-act="logout">${g('i-sliders')}<span>退出登录</span>${g('i-right', 'ic xs')}</div>
    </div>
    <div class="ver">主厨到家 v1.0.0 · 前后端在线版</div>
  </div></div>${tabbar('me')}</div>`;
};

/* ---------------- 渲染 / 导航 ---------------- */
const app = document.getElementById('app');
function render() {
  app.innerHTML = (V[S.view] || V.home)();
  if (S.view === 'msgs') app.scrollTop = 0;
}
function go(v, push = true) {
  if (push && v !== S.view) S.stack.push(S.view);
  S.view = v; render();
}
function back() { S.view = S.stack.pop() || 'home'; render(); }

let toastTimer = null;
function toast(msg) {
  const el = $('#toast');
  el.textContent = msg; el.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('on'), 2000);
}

const mask = $('#mask'), sheetEl = $('#sheet');
function closeSheet() { mask.classList.remove('on'); sheetEl.classList.remove('on'); }
mask.addEventListener('click', closeSheet);

/* ---------------- 数据加载 ---------------- */
async function loadMe() {
  S.user = await api('/me');
}
async function loadHome() {
  S.home = await api('/home');
  if (S.view === 'splash' || S.view === 'login') { S.view = 'home'; S.stack = []; }
  render();
}
async function refreshOrders(tab = S.orderTab) {
  const d = await api('/orders?status=all');
  S.orders = d.items; S.counts = d.counts;
  if (tab) S.orderTab = tab;
  render();
}
async function openChef(id) {
  S.chef = null; S.chefId = id; go('detail');
  S.chef = await api('/chefs/' + id);
  S.pkgId = S.chef.packages[0].id;
  if (S.view === 'detail') render();
}
async function openBooking() {
  if (!S.dates.length) {
    const d = await api('/dates');
    S.dates = d.items;
    S.date = S.dates[0].date;
  }
  await loadSlots();
  go('booking');
}
async function loadSlots() {
  const d = await api('/availability?chef_id=' + S.chefId + '&date=' + S.date);
  S.slots = d.slots;
  const free = S.slots.find(s => !s.disabled);
  if (!S.slots.find(s => s.slot === S.slot && !s.disabled)) S.slot = free ? free.slot : S.slots[0].slot;
}
async function openOrder(id) {
  S.order = null; S.orderId = id; go('order');
  S.order = await api('/orders/' + id);
  if (S.view === 'order') render();
}
async function openReview(order) {
  S.order = order; S.rating = 5; S.reviewTags = []; S.reviewText = '';
  go('review');
}
async function openStats(r) {
  if (r) S.range = r;
  S.stats = await api('/stats?range=' + S.range);
  if (!S.orders.length) { const d = await api('/orders?status=all'); S.orders = d.items; S.counts = d.counts; }
  render();
}
async function openMessages(tab) {
  if (tab) S.msgTab = tab;
  S.messages = await api('/messages?category=all');
  S.unread = S.messages.unread;
  render();
}

/* ---------------- 事件 ---------------- */
app.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-act]');
  if (!t) return;
  const act = t.dataset.act;
  const [k, a] = act.includes(':') ? [act.slice(0, act.indexOf(':')), act.slice(act.indexOf(':') + 1)] : [act, ''];
  try {
    switch (k) {
      case 'enter': S.view = 'login'; render(); break;
      case 'code': {
        const phone = ($('#phone').value || '').replace(/\D/g, '');
        if (phone.length !== 11) { toast('请输入 11 位手机号，如 13800000000'); break; }
        if (S.codeCooling) break;
        const d = await api('/auth/code', { method: 'POST', body: JSON.stringify({ phone }) });
        S.phone = phone;
        if (d.devCode) { S.codeValue = d.devCode; toast('验证码已发送并自动填入：' + d.devCode); }
        else toast('验证码已发送');
        S.codeCooling = 60; render();
        const iv = setInterval(() => {
          S.codeCooling -= 1;
          const btn = document.querySelector('.field .code');
          if (S.codeCooling <= 0 || !btn) { clearInterval(iv); S.codeCooling = 0;
            if (btn) { btn.textContent = '获取验证码'; btn.classList.remove('off'); } }
          else btn.textContent = `重新获取(${S.codeCooling}s)`;
        }, 1000);
        break;
      }
      case 'login': {
        const phone = ($('#phone').value || '').replace(/\D/g, '');
        const code = ($('#code').value || '').replace(/\D/g, '');
        if (phone.length !== 11) { toast('请输入 11 位手机号'); break; }
        if (code.length !== 6) { toast('请输入 6 位验证码'); break; }
        const d = await api('/auth/login', { method: 'POST', body: JSON.stringify({ phone, code }) });
        S.token = d.token; localStorage.setItem('zc_token', d.token); S.user = d.user;
        toast('登录成功，欢迎回家');
        S.view = 'home'; S.stack = [];
        await loadHome(); await refreshOrders();
        break;
      }
      case 'logout':
        S.token = ''; localStorage.removeItem('zc_token'); S.user = null;
        S.view = 'login'; S.stack = []; render(); toast('已退出登录');
        break;
      case 'tab': {
        const v = a;
        if (v === 'home') { S.view = 'home'; S.stack = []; render(); if (!S.home) loadHome(); }
        else if (v === 'orders') { S.view = 'orders'; S.stack = []; render(); await refreshOrders(); }
        else if (v === 'msgs') { S.view = 'msgs'; S.stack = []; render(); await openMessages(); }
        else if (v === 'me') { S.view = 'me'; S.stack = []; render(); if (!S.stats) { S.stats = null; openStats(); } if (!S.orders.length) refreshOrders(); }
        break;
      }
      case 'cat': S.cat = a; S.view = 'list'; render(); break;
      case 'sort': S.sortBy = a; render(); break;
      case 'chef': await openChef(+a); break;
      case 'pkg': S.pkgId = +a; render(); break;
      case 'go':
        if (a === 'booking') await openBooking();
        else if (a === 'stats') { go('stats'); await openStats(); }
        else if (a === 'home') { S.view = 'home'; S.stack = []; render(); }
        else { go(a); }
        break;
      case 'date': S.date = a; await loadSlots(); render(); break;
      case 'slot': S.slot = a; render(); break;
      case 'people': S.people = Math.max(2, Math.min(10, S.people + Number(a))); render(); break;
      case 'taste': {
        const i = S.tastes.indexOf(a); i >= 0 ? S.tastes.splice(i, 1) : S.tastes.push(a); render(); break;
      }
      case 'submit': {
        const p = S.chef.packages.find(q => q.id === S.pkgId);
        const btn = t; btn.textContent = '提交中…';
        const o = await api('/orders', { method: 'POST', body: JSON.stringify({
          chefId: S.chef.id, packageId: p.id, date: S.date, slot: S.slot, people: S.people,
          tastes: S.tastes, address: '郑州市金水区农业路 12 号 绿地老街 3 号楼 2 单元 1802',
          contact: S.user ? S.user.nickname : '张晓宁', note: ''
        }) });
        toast('预约已提交，已通知厨师接单');
        S.order = o; S.orderTab = 'pending'; S.stack = ['orders'];
        S.view = 'order'; render();
        await refreshOrders();
        await openMessages();
        S.view = 'order'; render();
        break;
      }
      case 'otab': S.orderTab = a; render(); break;
      case 'otabgo': S.orderTab = a; S.view = 'orders'; S.stack = []; render(); await refreshOrders(a); break;
      case 'order': await openOrder(a); break;
      case 'review': {
        const o = S.orders.find(x => x.id === a) || await api('/orders/' + a);
        await openReview(o); break;
      }
      case 'cancel': {
        mask.classList.add('on');
        sheetEl.innerHTML = `<b>取消这次预约？</b><p>已接单的订单取消将收取 10% 服务费，其余费用原路退回。</p>
          <div style="display:flex;gap:10px;margin-top:16px"><button class="btn-g" style="flex:1" data-cancel="1">再想想</button>
          <button class="btn-p" style="flex:1;height:44px;font-size:14px" data-ok="1">确认取消</button></div>`;
        sheetEl.classList.add('on'); break;
      }
      case 'star': {
        const rt = $('#rtext'); if (rt) S.reviewText = rt.value;
        S.rating = +a; render(); break;
      }
      case 'rt': {
        const rt = $('#rtext'); if (rt) S.reviewText = rt.value;
        const i = S.reviewTags.indexOf(a); i >= 0 ? S.reviewTags.splice(i, 1) : S.reviewTags.push(a); render(); break;
      }
      case 'anon': {
        const rt = $('#rtext'); if (rt) S.reviewText = rt.value;
        S.anonymous = !S.anonymous; render(); break;
      }
      case 'revsubmit': {
        const rt = $('#rtext'); if (rt) S.reviewText = rt.value;
        await api('/orders/' + S.order.id + '/review', { method: 'POST', body: JSON.stringify({
          rating: S.rating, tags: S.reviewTags, content: S.reviewText, anonymous: S.anonymous }) });
        toast('评价已提交，20 积分已到账');
        S.view = 'orders'; S.orderTab = 'done'; S.stack = []; render();
        await refreshOrders('done');
        break;
      }
      case 'range': await openStats(a); break;
      case 'mtab': S.msgTab = a; render(); break;
      case 'readall':
        await api('/messages/read-all', { method: 'POST' }); S.unread = 0;
        await openMessages(); toast('已标记全部已读'); break;
      case 'back': back(); break;
      case 'toast': toast(a); break;
    }
  } catch (err) {
    toast(err.message || '操作失败');
    if (S.view === 'booking') render();
  }
});

document.addEventListener('click', async (e) => {
  if (e.target.closest('[data-cancel]')) return closeSheet();
  const ok = e.target.closest('[data-ok]');
  if (ok) {
    closeSheet();
    try {
      const d = await api('/orders/' + S.orderId + '/cancel', { method: 'POST' });
      toast('预约已取消，' + money(d.refund) + ' 将原路退回');
      await refreshOrders();
      await openOrder(S.orderId);
    } catch (err) { toast(err.message); }
  }
});

/* 输入框保持状态 */
app.addEventListener('input', (e) => {
  if (e.target.id === 'phone') S.phone = e.target.value;
  if (e.target.id === 'code') S.codeValue = e.target.value;
  if (e.target.id === 'rtext') S.reviewText = e.target.value;
});

/* ---------------- 启动 ---------------- */
(async function boot() {
  render();
  if (S.token) {
    try {
      await loadMe();
      await loadHome();
      await refreshOrders();
      await openMessages();
      S.view = 'home'; render();
    } catch (e) {
      S.view = 'login'; render();
    }
  }
})();
</script>
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
</script>
</body>
</html>
"""
SW_JS = r"""const CACHE = 'zhuchu-v1';
const SHELL = ['/', '/index.html', '/style.css', '/extra.css', '/app.js', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    .then(() => self.clients.claim())));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return; // 接口永远走网络
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => caches.match('/index.html')));
    return;
  }
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
"""
MANIFEST = {"name": "主厨到家 · 上门私厨预约", "short_name": "主厨到家", "lang": "zh-CN", "start_url": "/", "scope": "/", "display": "standalone", "orientation": "portrait", "background_color": "#F5F7F9", "theme_color": "#12293D", "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"}, {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}

@app.get("/icon-192.png")
def icon192():
    from fastapi import Response
    return Response(base64.b64decode(ICON192), media_type="image/png")

@app.get("/icon-512.png")
def icon512():
    from fastapi import Response
    return Response(base64.b64decode(ICON512), media_type="image/png")

@app.get("/apple-touch-icon.png")
def icon180():
    from fastapi import Response
    return Response(base64.b64decode(ICON180), media_type="image/png")

@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(MANIFEST)

@app.get("/sw.js")
def service_worker():
    from fastapi import Response
    return Response(SW_JS, media_type="application/javascript")

@app.get("/")
def index():
    from fastapi import Response
    return Response(WEB_HTML, media_type="text/html")


ICON192 = "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAYAAABS3GwHAAAOfElEQVR4nO3de3SU5Z0H8G+GJJMQQiaZJNwJISZAuAqoQETUSrTSalTa6q5tbd1dl+1pd4/27Dm6umvr7h942uqp2mNv1rZatBcV8VZusUghEwy5ACERkpCQeyYhVyaXuewfGGCSmeF9X2Yyk+f3/ZzjCZl5n/f95TjfeZ7nvUalLNnoAZFQpnAXQBRODACJxgCQaAwAicYAkGgMAInGAJBoDACJxgCQaAwAicYAkGgMAInGAJBoDACJxgCQaAwAicYAkGgMAInGAJBoDACJFh2Kle74dlooVkuEB17pCOr62AOQaCHpAUYFO60kV6hGFewBSDQGgERjAEg0BoBEYwBINAaARGMASDQGgERjAEg0BoBEYwBINAaARGMASDQGgERjAEg0BoBEYwBINAaARGMASDQGgERjAEg0BoBEYwBINAaARAvpjbEiQUxMNCyJCeEuQwkjThe6e/vDXUZQKR+AG1YuxhsvPBnuMpRQXF6Fe//16XCXEVQcApFoDACJxgCQaAwAicYAkGgMAInGAJBoDACJxgCQaAwAicYAkGjKnwtkxNDwCLI2fT3cZdAEYAAESLEkIiUpEW6PB263B26PGx73pX9f+OmBx+2+tIzbg97+gXCXHnIMgAAPFtyG/3zka7raSOkFGYAApphMmJWeEu4yAABujwfNbZ3hLkM5DEAAaVYLit5+MdxlAAB6+weQu/nhcJehHO4FItEYABKNASDRGAASjQEg0RgAEo0BINEYABKNASDRGAASjadCBNDa0YW56+8PdxkUQgzAJPKr7Y/hjpuum5BtmWNj0Hj4jXGv3/PI/+BIRfWE1DAROAQi0ZTvAaKioq6q/ePbHsB3vnF3kKox5tmfv4mfvvp2WGtQlfI9gJHPv8vlDn4hFJEEBED/n+hyMwBSCAiA/jYeDwMghfIBMJkM9AAcAomhfAAMzQHcnuAXQhFJ+QCYDMwBPJwDiKF8AKKjp+huM+J0haASikTKByA2Rv+hjsGh4RBUQpFI+QNhMdH6/8Sh4RHD23vmhdfw/v4iv+9nZczG688/YXj9FFzqB2CCe4Cu7j40ttr9vj91apzhdT/6zMt4PPZXutv98/1b8G9fv0tXG6fLhce3j9/WmcZW3duPZMoHwMgQaGg4ModARu/V2X/eobuNy+XGjl2FhrY3mSg/BzAyBBocMj4ECpeczLm4Y9PEnCmqEuV7gPi4WN1tBiO0BxhrzoxU3L15Awry85CbnYHzjiGsuWsb+vrPh7u0SUP5AEybGq+7TSTvBUqxJGLLLetQkJ+H61cu8jrbdWq8GV/dsgm/fvPDMFY4uSgfgAQDAejti6xv0IT4OBTk5+Ge/Buxad0KRE/xf2zjoftuxyt//AgeD49ma6F8AKYl6N/rcjUPhnjuqW147qlthtv7oud6hMx5M3HT9SvwN1t5UGtQlfKTYCNDoEjrAbp7+3Ut/8Bdt4SoEvUoH4AEA/vdeyLs0UDv77fpGtLcvnEtkpMSQ1iROpQPgAo9QFObHbayk5qXj4mJRkF+XggrUofyAUhOmqa7TSQ+HO6tv/5d1/L3f/nm0BSiGOUDYE1O0t2mJ8J6AAB4b18Rhke0H6Bbmr0AOZlzQ1iRGpQOgDk2BokJ+odAeiedE6G3fwB/s1XoavOlW9eFqBp1KB0Aa/J0Q+3aO88FuZLg2H3gU13Lf/m29SGqRB1KByA1Rf/wp/+8A+cdQyGo5urtOVgCt47LNbMXzEHOQg6DAlH6QFiqgfF/u73b6/eXfv8ufv/2niBVdMGcmano6OrBsMbrDno+P7fHfq4XR0+cwtrlOZq3defN1+sKjTRKByDNQA8wdvjT2z8QcK9QclIiVixeiGuXZmHB3Jn4jx/+7Irb+MmT27BmeQ5KT5xCcXk1isurUHL8lKaT2HYf+FRXADbdsBKFh8s0Ly+N0gGYMzNVd5u2MT3A5eLjzFiWswCrcrOwMjcL1+Zeg4w5M7yW+cWO91F5qj7gNhZnzYc5Ngbrrs3FumtzAQButwdVNQ0oLq/GkYoqHKmo9vlk+L2HjuKJ7/yD5r9n9bJsHD1+SvPy0igdgIzZM6680Bjtnd3jXnv04a24Y9N1WJQ1D1OucJ+h2zeuDRiANKsFKZbxR2lNpijkZmcgNzsDD23NBwA0ttpxpLwKz7/yFmoamgEAn9U2wn6uF6kaJ/hTTCZsvG65pmUlUnoSnDFXfwDa7F3jXpuVnoLc7IwrfvgBIP+mtQHfz71mvuZa5s5Mxd2b88YNwQ4fPaF5HQCQm52ha3lJ1A7A7HTdbXwNO4p0nIawfFFmwG/n1UuzddVTVFqJjq4er9cOlVTqWgf5p2wApsabkWa16G5Xd3b8Rd+2Uu0BAID1q5f6fW+NjgksALzn4w4Th0r09QDkn7IBmG9g/A/4DkBjq91nz+BP3lrfAYiKisLqZddoXo/b7cGHHxePe72moVnT0eqW9i789NW38fo7+zRvUxplJ8EL5ugPQEdXj987KFRU12L2DKum9Wzw0wNkZ87B9GkJmuuxlZ0cN/wZVXmqHhvWjN/OyIgTew6WYMeuQhywVcDlduN7D92jeZvSKBsAIxO/urMtft87VlWn+flcC+fPQoolEV3dfV6vb1idq6seX8OfUZWnvQNQXXsWb+wqxF8++mTcdsk/ZQOwfHGm7ja+hj+jKqpq9W1/Uea4k9fy1izT3N7t9uADH8OfUZWn6tE34MC7ew5hx65ClFWe1lUfXaBsAFYsXqi7TaAAHK+u07Wu5YsWegXAZIrCeh09QFFpJTp8HJMY9eHHxdi1rwiOwcg8b2myUHISnGa1YEZqsu52gW7719HVg54+7RfKrBjTAy3LyYRluvaLc3buPRTw/b4BBz/8QaBkAMZ++LQ6eboh4Pu1Df7nCGMtnD/L6/dbN6zS3NbpcuGDQv/DHwoeNQOwSP/wp/+8A7UBJsEALp6OoMW8MQfhvrBhtea2B2wVONfDiexEUDIAq3K172sfdayq7op3Xgg0RxgrIT7u4p0ZrJbpWLkkS3Pbv3z0ieZl6eooNwmOiYnWNdkcVX7yynt52u2BrxRzDA6h5Pgp2EpPoqjs5MXTm/sGHPjm97cjb80y5K1diqXZC2Ay+X542YBjEHsPHoXVMh3pqRakW5MxI9WCNKsF8WYzfvTLP+r+28g/5QKwbtUSTI03625XUVVzxWXau7q9fu/tH0BxefXFD/yxqjo4XeMfrzQ8MoLCw2UoPFwGc2wMcjLn4q7NG1CwOQ+z0lO8lo0zx+LEnl/7vP1hd28/AxBkygXglvWrDLXT0gOcbenAe/uLYCs9CVt5FapqGuB2ezDFZEJK8nQsWjgPadYkpKVYkGZNQrr1wjd4WkoS0qwWpFuTrngkONAZp+bYGN1/FwWmXABuXrdSd5ve/gHUN7V5vRYdPQVWy3SkJifBmpyINKsFaSlJaGyxY1XuNdi8ce3FD3ZKUqLfIU0wxZn13+odgNcdpMmbUgGYMyPV0L1wyiovffv/6aX/Rm52BpIStZ+zM1GioqIQGxOj6/5AAAwdE5HysHClAmB0+HOg+NKdlF1ud0R++EeZzRcCsGjhPHzlzk0YHBrG0PDw5z9HMDLihNvjgdvtgckUhXmz0vGVO2/SvR29IZuslArAVgP/owFg/2UXjZ8+04wb12o/Z2eixZlj0dd/Hg3N7fjHu29F4rSpIdmOYzByHxISTMocB1icNV/X3RJGtbR34bPaxou/6znYFQ7xn88DHIND+NMHB0K2Hfs536dhq0aZADxYcJuhdoWHS71+r6lvCkY5IXP5RPi1nXtDtp2GpvaQrTuSKBGA+Dgz7rvjRkNtC4u8n6Ryuj6ye4DLd4V+VtuIqprA5y8ZZSvXdxnoZKVEAAo2bzA0Fna6XDh45JjXay3tXRF9luXYXaHv7j0cku3YSqtCst5Io0QAjJz6AACfVlSjb8D7EkiPx4MaHWd9TiRf9ywNRQCa2uw4WRP45l6qUGIv0Pd+8BJe37kP37g3H1tuuQHR0f6foni5P3/g+6SzmvpmLMtZEMQKtTnvGEJjSwfOfv5fY2v7pX+3dPi81PFMYyvqzrYic97MoNXx1I9/I+Z+okoEAABsZVWwlVVh++x0/Pu37sV9X9wY8HGifQMOvLvP90UnNSGaBww4BtHUYtf1AdfiY1t50ALw0YEj2P1JSVDWNRkoE4BRDc3teOz/XsYLv30Hj/3TVhTk5/k8FeCd3Qf93gb9tME9Qed6+tDU1onG1g40t1742dhqv/itHqpz/A/YKvCtrbdf9Xp+99YePP3874JQ0eShXABGnWlsxXeffhGv/nk3nnn0IaxY4n2RzB927vfbtqZ+/BzA6XShub0TzW12NLV2oqnNjqY2Oxpb7Bdea7OH7bkCxeVXN2Gtb2rDsz9/Ezv3BL4MU0XKBmBUyfHPsOXh/8L9X7oZT373QSQlJuBYdR2OBbjIvaahGf/74muXPuitdnR0dUfsuLinbwANze2Yr+NWkH0DDhQeLsMfdu7D30tOiH2yvPIBAC7s2dmxqxB7D5XiR48/gj0HA49xB4eG8fLr701QdcFxvLoOs2dY4XS6MOJ0wel0YnBoBJ3dveg814uu7l602s/hRPUZVFTXou5sq9gP/eVEBGBUR2c3vvn97Zr3Ek0m//LEc+EuYVJS4jiAXk7n+Ku2SCaRASAaxQCQaAwAicYAkGgMAInGAJBoDACJxgCQaAwAicYAkGgMAInGAJBoDACJxgCQaAwAicYAkGgMAInGAJBoDACJxgCQaAwAicYAkGgMAInGAJBoDACJxgCQaAwAicYAkGgMAInGAJBoIX0+wI5vp4Vy9URXjT0AiRaVsmQjn5NDYrEHINEYABKNASDRGAASjQEg0RgAEo0BINEYABKNASDRGAASjQEg0RgAEo0BINEYABKNASDRGAASjQEg0RgAEo0BINH+H0m2lZlqjpLxAAAAAElFTkSuQmCC"
ICON512 = "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"
ICON180 = "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"
