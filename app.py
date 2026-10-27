import os, re, sqlite3
from datetime import datetime
from functools import wraps
from flask import Flask, g, session, redirect, url_for, request, flash, render_template, abort
from jinja2 import DictLoader
from markupsafe import Markup, escape
from werkzeug.security import generate_password_hash, check_password_hash

DB = os.environ.get("SKILLSWAP_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "skillswap.db"))
PRESET = ["Python", "吉他", "摄影", "英语", "日语", "钢琴", "UI设计", "视频剪辑",
          "篮球", "瑜伽", "烹饪", "写作", "Excel", "绘画", "演讲"]
ALIAS = {"py": "Python", "python": "Python", "guitar": "吉他", "photography": "摄影", "photo": "摄影",
         "english": "英语", "japanese": "日语", "piano": "钢琴", "ui": "UI设计", "设计": "UI设计",
         "剪辑": "视频剪辑", "excel": "Excel", "drawing": "绘画", "cooking": "烹饪", "yoga": "瑜伽"}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")


# ---------- 数据层 ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    c = g.pop("db", None)
    if c: c.close()


def init():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, nickname TEXT UNIQUE,
        pw TEXT, city TEXT DEFAULT '', contact TEXT DEFAULT '', bio TEXT DEFAULT '', created TEXT);
    CREATE TABLE IF NOT EXISTS skills(user_id INTEGER, kind TEXT, skill TEXT, UNIQUE(user_id,kind,skill));
    CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT, from_id INTEGER, to_id INTEGER,
        offer TEXT, want TEXT, message TEXT, status TEXT DEFAULT 'pending', created TEXT);
    """)
    c.commit(); c.close()


def now(): return datetime.now().strftime("%Y-%m-%d %H:%M")
def norm(s): s = s.strip(); return ALIAS.get(s.lower(), s)


def parse(selected, extra):
    return {norm(x) for x in list(selected) + re.split(r"[,，、;；\s]+", extra or "") if x.strip()}


def skills(uid, kind):
    return {r["skill"] for r in db().execute("SELECT skill FROM skills WHERE user_id=? AND kind=?", (uid, kind))}


def set_skills(uid, kind, items):
    d = db()
    d.execute("DELETE FROM skills WHERE user_id=? AND kind=?", (uid, kind))
    d.executemany("INSERT OR IGNORE INTO skills VALUES(?,?,?)", [(uid, kind, s) for s in items])
    d.commit()


def others(exclude=0):
    return [{**dict(r), "teach": skills(r["id"], "teach"), "learn": skills(r["id"], "learn")}
            for r in db().execute("SELECT * FROM users WHERE id!=? ORDER BY id DESC", (exclude,))]


def match(mt, ml, u):
    a, b = sorted(ml & u["teach"]), sorted(mt & u["learn"])   # a: 对方能教我  b: 我能教对方
    return a, b, min(len(a), 2) * 20 + min(len(b), 2) * 20 + (20 if a and b else 0)


def cur():
    uid = session.get("uid")
    return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not cur():
            flash("请先登录"); return redirect(url_for("login"))
        return f(*a, **k)
    return w


@app.context_processor
def helpers():
    def chips(items, cls):
        return Markup("".join(f'<span class="chip {cls}">{escape(i)}</span>' for i in sorted(items)) or "<i>暂未填写</i>")
    return dict(chips=chips, me=cur())


# ---------- 页面 ----------
@app.route("/")
def index():
    me, results = cur(), []
    if me:
        mt, ml = skills(me["id"], "teach"), skills(me["id"], "learn")
        for u in others(me["id"]):
            a, b, s = match(mt, ml, u)
            if s > 0:
                u["pending"] = bool(db().execute(
                    "SELECT 1 FROM requests WHERE from_id=? AND to_id=? AND status='pending'", (me["id"], u["id"])).fetchone())
                results.append(dict(u=u, a=a, b=b, s=s))
        results.sort(key=lambda r: -r["s"])
        return render_template("index.html", results=results, mt=sorted(mt), empty=not (mt or ml))
    return render_template("index.html", results=[], mt=[], empty=False)


@app.route("/browse")
def browse():
    kw = request.args.get("kw", "").strip()
    users = []
    for u in others(0 if not cur() else cur()["id"]):
        text = " ".join([u["nickname"], u["city"]] + list(u["teach"]) + list(u["learn"])).lower()
        if not kw or kw.lower() in text or norm(kw).lower() in text:
            users.append(u)
    return render_template("browse.html", users=users, kw=kw)


@app.route("/register", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    is_reg = request.path == "/register"
    if request.method == "POST":
        nick, pw = request.form["nickname"].strip(), request.form["pw"]
        if is_reg:
            if not nick or len(pw) < 4:
                flash("请填写昵称，口令至少 4 位")
            elif db().execute("SELECT 1 FROM users WHERE nickname=?", (nick,)).fetchone():
                flash("昵称已被使用")
            else:
                cur_ = db().execute("INSERT INTO users(nickname,pw,created) VALUES(?,?,?)",
                                    (nick, generate_password_hash(pw), now()))
                db().commit(); session["uid"] = cur_.lastrowid
                return redirect(url_for("profile"))
        else:
            u = db().execute("SELECT * FROM users WHERE nickname=?", (nick,)).fetchone()
            if u and check_password_hash(u["pw"], pw):
                session["uid"] = u["id"]; return redirect(url_for("index"))
            flash("昵称或口令错误")
    return render_template("login.html", is_reg=is_reg)


@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("index"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    me = cur()
    if request.method == "POST":
        f = request.form
        db().execute("UPDATE users SET city=?,contact=?,bio=? WHERE id=?",
                     (f["city"].strip(), f["contact"].strip(), f["bio"].strip(), me["id"])); db().commit()
        set_skills(me["id"], "teach", parse(f.getlist("teach"), f["teach_x"]))
        set_skills(me["id"], "learn", parse(f.getlist("learn"), f["learn_x"]))
        flash("档案已保存"); return redirect(url_for("index"))
    t, l = skills(me["id"], "teach"), skills(me["id"], "learn")
    return render_template("profile.html", u=me, t=t, l=l, opts=sorted(set(PRESET) | t | l))


@app.route("/request/<int:uid>", methods=["POST"])
@login_required
def send_request(uid):
    me = cur()
    want, offer = request.form["want"], request.form["offer"]
    if uid == me["id"] or want not in skills(uid, "teach") or offer not in skills(me["id"], "teach"):
        abort(400)
    db().execute("INSERT INTO requests(from_id,to_id,offer,want,message,created) VALUES(?,?,?,?,?,?)",
                 (me["id"], uid, offer, want, request.form.get("message", "")[:200], now())); db().commit()
    flash("申请已发送"); return redirect(url_for("index"))


@app.route("/requests")
@login_required
def requests_page():
    me = cur()
    q = "SELECT r.*, u.nickname, u.contact FROM requests r JOIN users u ON u.id=r.{} WHERE r.{}=? ORDER BY r.id DESC"
    return render_template("requests.html",
                           inbox=db().execute(q.format("from_id", "to_id"), (me["id"],)).fetchall(),
                           sent=db().execute(q.format("to_id", "from_id"), (me["id"],)).fetchall())


@app.route("/requests/<int:rid>/<act>", methods=["POST"])
@login_required
def handle(rid, act):
    if act not in ("accept", "decline"): abort(400)
    r = db().execute("SELECT * FROM requests WHERE id=? AND to_id=? AND status='pending'", (rid, cur()["id"])).fetchone()
    if not r: abort(404)
    db().execute("UPDATE requests SET status=? WHERE id=?", ("accepted" if act == "accept" else "declined", rid)); db().commit()
    return redirect(url_for("requests_page"))


@app.route("/seed", methods=["POST"])
def seed():
    demo = [("小林", "上海", "wx: xiaolin", ["摄影", "视频剪辑"], ["Python", "英语"]),
            ("阿杰", "北京", "wx: ajie", ["Python", "Excel"], ["吉他", "摄影"]),
            ("Mia", "深圳", "mia@example.com", ["英语", "钢琴"], ["Python", "瑜伽"]),
            ("老周", "杭州", "wx: laozhou", ["吉他", "烹饪"], ["摄影", "写作"])]
    for n, city, ct, t, l in demo:
        if db().execute("SELECT 1 FROM users WHERE nickname=?", (n,)).fetchone(): continue
        uid = db().execute("INSERT INTO users(nickname,pw,city,contact,bio,created) VALUES(?,?,?,?,?,?)",
                           (n, generate_password_hash("1234"), city, ct, "示例用户", now())).lastrowid
        db().commit(); set_skills(uid, "teach", t); set_skills(uid, "learn", l)
    flash("已载入 4 位示例用户（口令均为 1234）"); return redirect(request.referrer or url_for("browse"))


# ---------- 模板 ----------
T = {}
T["base.html"] = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SkillSwap 技能交换站</title><style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#f6f7fb;color:#222;margin:0}
.wrap{max-width:720px;margin:0 auto;padding:16px}
nav{background:#fff;border-bottom:1px solid #e5e7eb}nav .wrap{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
nav a{color:#444;text-decoration:none}nav b{font-size:1.1rem;margin-right:auto}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:12px 0}
.chip{display:inline-block;padding:2px 10px;margin:2px 4px 2px 0;border-radius:12px;font-size:.85rem}
.t{background:#e6f4ea;color:#137333}.l{background:#e8f0fe;color:#1a56c4}
.badge{background:#fff3cd;color:#8a6100;padding:2px 10px;border-radius:12px;font-size:.8rem;font-weight:600}
.flash{background:#e8f0fe;padding:10px 14px;border-radius:8px;margin:12px 0}.muted{color:#777;font-size:.9rem}
input,select,textarea{width:100%;box-sizing:border-box;padding:8px;margin:4px 0 10px;border:1px solid #ccc;border-radius:6px;font:inherit}
button{background:#4f46e5;color:#fff;border:0;padding:8px 16px;border-radius:6px;cursor:pointer;font:inherit}
button.g{background:#e5e7eb;color:#333}.row{display:flex;justify-content:space-between;align-items:center}
label.cb{display:inline-block;margin:0 12px 6px 0;font-weight:400}label.cb input{width:auto;margin:0 4px 0 0}
</style></head><body><nav><div class="wrap"><b>🔁 SkillSwap</b>
<a href="{{ url_for('index') }}">为我匹配</a><a href="{{ url_for('browse') }}">浏览</a>
{% if me %}<a href="{{ url_for('profile') }}">我的档案</a><a href="{{ url_for('requests_page') }}">交换申请</a>
<a href="{{ url_for('logout') }}">退出（{{ me.nickname }}）</a>
{% else %}<a href="{{ url_for('login') }}">登录</a><a href="{{ url_for('login') }}register">注册</a>{% endif %}
</div></nav><div class="wrap">{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}
{% block body %}{% endblock %}
<form method="post" action="{{ url_for('seed') }}" style="margin-top:30px"><button class="g">载入示例数据</button></form>
</div></body></html>"""

T["login.html"] = """{% extends 'base.html' %}{% block body %}
<h2>{{ '注册' if is_reg else '登录' }}</h2><div class="card"><form method="post">
昵称<input name="nickname" required>口令（4位以上）<input name="pw" type="password" required>
<button>{{ '注册' if is_reg else '登录' }}</button></form>
<p class="muted">{% if is_reg %}已有账号？<a href="/login">去登录</a>{% else %}没有账号？<a href="/register">去注册</a>{% endif %}</p></div>{% endblock %}"""

T["index.html"] = """{% extends 'base.html' %}{% block body %}
<h2>技能交换站</h2><p class="muted">我教你摄影，你教我 Python —— 用技能换技能。</p>
{% if not me %}<div class="card">请先 <a href="/register">注册</a> 或 <a href="/login">登录</a>，填写档案后即可获得匹配。</div>
{% elif empty %}<div class="card">先去 <a href="/profile">我的档案</a> 填写你能教和想学的技能。</div>
{% elif not results %}<div class="card">暂时没有匹配对象，试试补充技能，或邀请朋友加入。</div>{% endif %}
{% for r in results %}{% set u = r.u %}<div class="card">
<div class="row"><b>{{ u.nickname }} · {{ u.city or '城市未填' }}</b><span>匹配度 <b>{{ r.s }}</b></span></div>
{% if r.a and r.b %}<span class="badge">🔁 双向匹配</span>{% endif %}
<div>能教：{{ chips(u.teach,'t') }}</div><div>想学：{{ chips(u.learn,'l') }}</div>
<div class="muted">匹配理由：{% if r.a %}TA 能教你：{{ r.a|join('、') }}；{% endif %}{% if r.b %}你能教 TA：{{ r.b|join('、') }}{% endif %}</div>
{% if u.pending %}<div class="muted">✅ 已发送申请，等待对方回应</div>
{% elif not mt or not u.teach %}<div class="muted">双方都需要有至少一项「能教」的技能才能发起交换</div>
{% else %}<details><summary>发起交换申请</summary><form method="post" action="{{ url_for('send_request', uid=u.id) }}">
我想学<select name="want">{% for s in u.teach|sort %}<option {{ 'selected' if r.a and s==r.a[0] }}>{{ s }}</option>{% endfor %}</select>
我来教<select name="offer">{% for s in mt %}<option {{ 'selected' if r.b and s==r.b[0] }}>{{ s }}</option>{% endfor %}</select>
留言<textarea name="message" maxlength="200" placeholder="简单介绍自己、希望的交流方式"></textarea>
<button>发送申请</button></form></details>{% endif %}</div>{% endfor %}{% endblock %}"""

T["browse.html"] = """{% extends 'base.html' %}{% block body %}
<h2>浏览</h2><form><input name="kw" value="{{ kw }}" placeholder="搜索昵称 / 城市 / 技能，例如：Python"></form>
{% for u in users %}<div class="card"><b>{{ u.nickname }} · {{ u.city or '城市未填' }}</b>
{% if u.bio %}<div class="muted">{{ u.bio }}</div>{% endif %}
<div>能教：{{ chips(u.teach,'t') }}</div><div>想学：{{ chips(u.learn,'l') }}</div></div>
{% else %}<div class="card">没有找到用户，可以点页面底部「载入示例数据」。</div>{% endfor %}{% endblock %}"""

T["profile.html"] = """{% extends 'base.html' %}{% block body %}
<h2>我的档案</h2><div class="card"><form method="post">
城市<input name="city" value="{{ u.city }}">
联系方式（仅在双方同意交换后才会互相显示）<input name="contact" value="{{ u.contact }}">
一句话介绍<input name="bio" maxlength="120" value="{{ u.bio }}">
<p><b>我能教的</b></p>{% for o in opts %}<label class="cb"><input type="checkbox" name="teach" value="{{ o }}" {{ 'checked' if o in t }}>{{ o }}</label>{% endfor %}
<input name="teach_x" placeholder="其他能教的技能（逗号分隔）">
<p><b>我想学的</b></p>{% for o in opts %}<label class="cb"><input type="checkbox" name="learn" value="{{ o }}" {{ 'checked' if o in l }}>{{ o }}</label>{% endfor %}
<input name="learn_x" placeholder="其他想学的技能（逗号分隔）"><button>保存档案</button></form></div>{% endblock %}"""

T["requests.html"] = """{% extends 'base.html' %}{% block body %}
<h2>收到的申请</h2>{% for r in inbox %}<div class="card"><b>{{ r.nickname }}</b> 想学你的 <b>{{ r.want }}</b>，并教你 <b>{{ r.offer }}</b>
{% if r.message %}<div class="muted">留言：{{ r.message }}</div>{% endif %}
{% if r.status == 'pending' %}<div class="row" style="justify-content:flex-start;gap:8px">
<form method="post" action="{{ url_for('handle', rid=r.id, act='accept') }}"><button>✅ 接受</button></form>
<form method="post" action="{{ url_for('handle', rid=r.id, act='decline') }}"><button class="g">婉拒</button></form></div>
{% elif r.status == 'accepted' %}<div class="flash">已接受 · 对方联系方式：{{ r.contact or '对方未填写' }}</div>
{% else %}<div class="muted">已婉拒</div>{% endif %}</div>{% else %}<p class="muted">暂无</p>{% endfor %}
<h2>我发出的申请</h2>{% for r in sent %}<div class="card">向 <b>{{ r.nickname }}</b> 申请：学 <b>{{ r.want }}</b> ⇄ 教 <b>{{ r.offer }}</b>
{% if r.status == 'accepted' %}<div class="flash">对方已接受 · 联系方式：{{ r.contact or '对方未填写' }}</div>
{% elif r.status == 'pending' %}<div class="muted">⏳ 等待回应</div>{% else %}<div class="muted">已婉拒</div>{% endif %}</div>
{% else %}<p class="muted">暂无</p>{% endfor %}{% endblock %}"""

app.jinja_loader = DictLoader(T)
init()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
