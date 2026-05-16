import streamlit as st
from supabase import create_client
import os

st.set_page_config(
    page_title="AI 뉴스 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] > .main { background: #f8f8f6; }
[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #e8e8e4; }
[data-testid="stSidebar"] .stButton button { text-align: left; justify-content: flex-start; }
div[data-testid="stMetric"] {
    background: #fff;
    border: 0.5px solid #e0e0dc;
    border-radius: 10px;
    padding: 14px 18px;
}
.art-html a { color: #185FA5; text-decoration: none; }
.art-html a:hover { text-decoration: underline; }
</style>
""", unsafe_allow_html=True)

# ── Supabase ──────────────────────────────────────────────────────────────────

@st.cache_resource
def get_sb():
    url = os.getenv('SUPABASE_URL') or st.secrets.get('SUPABASE_URL', '')
    key = os.getenv('SUPABASE_KEY') or st.secrets.get('SUPABASE_KEY', '')
    if not url or not key:
        st.error("SUPABASE_URL / SUPABASE_KEY 환경 변수가 없어요.")
        st.stop()
    return create_client(url, key)

sb = get_sb()

# ── Data helpers ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_reports():
    return sb.table('reports').select('date,total,counts').order('date', desc=True).execute().data

@st.cache_data(ttl=300)
def get_report(d):
    res = sb.table('reports').select('*').eq('date', d).execute()
    return res.data[0] if res.data else None

@st.cache_data(ttl=30)
def get_folders():
    return sb.table('folders').select('*').order('created_at').execute().data

@st.cache_data(ttl=30)
def get_bookmarks(folder_id=None):
    q = sb.table('bookmarks').select('*, folders(name,color)').order('created_at', desc=True)
    if folder_id:
        q = q.eq('folder_id', folder_id)
    return q.execute().data

# ── Session state ─────────────────────────────────────────────────────────────

for k, v in [('view', 'report'), ('sel_date', None), ('sel_folder', None)]:
    if k not in st.session_state:
        st.session_state[k] = v

if 'date' in st.query_params and st.session_state.sel_date is None:
    st.session_state.sel_date = st.query_params['date']

# ── Dialogs ───────────────────────────────────────────────────────────────────

@st.dialog("💾 폴더에 저장")
def dlg_save_article(article):
    folders = get_folders()
    sel = st.selectbox("폴더 선택", [f['name'] for f in folders])
    if st.button("저장하기", type="primary", use_container_width=True):
        f = next(x for x in folders if x['name'] == sel)
        sb.table('bookmarks').insert({
            'type': 'article',
            'title': article['title'],
            'url': article.get('url', ''),
            'source': article.get('source', ''),
            'summary': article.get('summary', '')[:300],
            'report_date': st.session_state.sel_date,
            'folder_id': f['id'],
        }).execute()
        get_bookmarks.clear()
        st.rerun()

@st.dialog("📌 리포트 저장")
def dlg_save_report(report):
    folders = get_folders()
    sel = st.selectbox("폴더 선택", [f['name'] for f in folders])
    if st.button("저장하기", type="primary", use_container_width=True):
        f = next(x for x in folders if x['name'] == sel)
        sb.table('bookmarks').insert({
            'type': 'report',
            'title': f"{report['date']} AI 리포트",
            'report_date': report['date'],
            'folder_id': f['id'],
        }).execute()
        get_bookmarks.clear()
        st.rerun()

@st.dialog("📁 새 폴더 만들기")
def dlg_new_folder():
    name = st.text_input("폴더 이름")
    color = st.color_picker("색상", "#185FA5")
    if st.button("만들기", type="primary", use_container_width=True):
        if name.strip():
            sb.table('folders').insert({'name': name.strip(), 'color': color}).execute()
            get_folders.clear()
            st.rerun()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### 📊 AI 뉴스")
    tab_hist, tab_bm = st.tabs(["📰 히스토리", "📁 북마크"])

    with tab_hist:
        reps = get_reports()
        if not reps:
            st.caption("아직 리포트가 없어요")
        else:
            if st.session_state.sel_date is None:
                st.session_state.sel_date = reps[0]['date']
            for r in reps:
                d = r['date']
                active = (d == st.session_state.sel_date and st.session_state.view == 'report')
                label = f"{'▶ ' if active else ''}{d}  ({r['total']}개)"
                if st.button(label, key=f"r_{d}", use_container_width=True,
                             type="primary" if active else "secondary"):
                    st.session_state.update(view='report', sel_date=d)
                    st.query_params['date'] = d
                    st.rerun()

    with tab_bm:
        folders = get_folders()
        all_active = (st.session_state.view == 'bookmarks' and st.session_state.sel_folder is None)
        if st.button("전체 북마크", use_container_width=True,
                     type="primary" if all_active else "secondary"):
            st.session_state.update(view='bookmarks', sel_folder=None)
            st.rerun()

        for f in folders:
            active = (st.session_state.view == 'bookmarks'
                      and st.session_state.sel_folder
                      and st.session_state.sel_folder['id'] == f['id'])
            if st.button(f"📁 {f['name']}", key=f"f_{f['id']}",
                         use_container_width=True,
                         type="primary" if active else "secondary"):
                st.session_state.update(view='bookmarks', sel_folder=f)
                st.rerun()

        st.divider()
        if st.button("+ 새 폴더", use_container_width=True):
            dlg_new_folder()

# ── REPORT VIEW ───────────────────────────────────────────────────────────────

if st.session_state.view == 'report':
    if not st.session_state.sel_date:
        st.info("왼쪽에서 날짜를 선택해주세요")
        st.stop()

    report = get_report(st.session_state.sel_date)
    if not report:
        st.warning("해당 날짜 리포트를 찾을 수 없어요")
        st.stop()

    arts     = report['articles']
    digest   = report['digest']
    clusters = digest['clusters']
    must_reads = digest.get('must_reads', [])
    expls    = digest.get('explanations', {})
    counts   = report['counts']

    # Header
    hc1, hc2 = st.columns([7, 1])
    with hc1:
        st.markdown(f"## {report['generated_at']}")
    with hc2:
        if st.button("📌 리포트 저장", use_container_width=True):
            dlg_save_report(report)

    # Stats
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체 수집", f"{report['total']}개")
    c2.metric("논문", f"{counts.get('논문', 0)}편")
    c3.metric("뉴스", f"{counts.get('뉴스', 0)}건")
    c4.metric("커뮤니티", f"{counts.get('커뮤니티', 0)}개")

    st.divider()

    # Must reads
    st.markdown("### ⭐ 오늘 꼭 읽어야 할 글")
    for i, mr in enumerate(must_reads):
        idx = mr['index']
        if idx >= len(arts):
            continue
        a   = arts[idx]
        exp = expls.get(str(idx), '')
        with st.container(border=True):
            nc, tc, bc = st.columns([1, 9, 2])
            with nc:
                st.markdown(
                    f"<p style='font-size:36px;font-weight:800;color:#ebebeb;margin:0;line-height:1'>0{i+1}</p>",
                    unsafe_allow_html=True)
            with tc:
                st.markdown(f"**[{a['title']}]({a['url']})**")
                st.markdown(f"📌 {mr['why']}")
                st.markdown(f":blue[↗ {mr['hook']}]")
                if exp:
                    st.caption(exp)
            with bc:
                st.write("")
                if st.button("💾 저장", key=f"mr_{i}"):
                    dlg_save_article(a)

    st.divider()

    # Flow diagram
    st.markdown("### 🔀 오늘의 흐름")
    fc1, fc2, fc3 = st.columns(3)

    with fc1:
        st.caption("소스")
        for c in clusters:
            srcs = {}
            for idx in c['indices']:
                if idx < len(arts) and arts[idx]['source'] not in srcs:
                    srcs[arts[idx]['source']] = arts[idx]['title']
            for sname, stitle in srcs.items():
                st.markdown(
                    f"""<div style="background:#fff;border:0.5px solid #d8d8d4;border-radius:7px;
                    padding:7px 10px;margin-bottom:5px;font-size:12px">
                    <b style="color:#444">{sname}</b><br>
                    <span style="color:#aaa;font-size:11px">{stitle[:40]}{'…' if len(stitle)>40 else ''}</span>
                    </div>""", unsafe_allow_html=True)
            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    with fc2:
        st.caption("클러스터")
        for c in clusters:
            st.markdown(
                f"""<div style="background:{c['bg']};color:{c['textColor']};
                border-radius:10px;padding:12px 14px;margin-bottom:14px">
                <b style="font-size:13px">{c['label']}</b><br>
                <span style="font-size:11px;opacity:.75">{c['desc']}</span><br>
                <span style="font-size:10px;opacity:.5">{len(c['indices'])}개 글</span>
                </div>""", unsafe_allow_html=True)

    with fc3:
        st.caption("테마")
        for c in clusters:
            st.markdown(
                f"""<div style="background:{c['themeBg']};color:{c['themeColor']};
                border-radius:10px;padding:12px 14px;margin-bottom:14px">
                <b style="font-size:13px">{c['theme']}</b>
                </div>""", unsafe_allow_html=True)

    st.divider()

    # Cluster article lists
    st.markdown("### 📋 클러스터별 전체 글")
    clustered = {idx for c in clusters for idx in c['indices']}

    for c in clusters:
        with st.expander(f"**{c['label']}** — {c['desc']}  ({len(c['indices'])}개)"):
            for idx in c['indices']:
                if idx >= len(arts):
                    continue
                a   = arts[idx]
                exp = expls.get(str(idx), '')
                ac, sc = st.columns([11, 1])
                with ac:
                    st.markdown(
                        f"""<div class="art-html" style="border-top:1px solid #f2f2ee;padding:10px 0">
                        <span style="background:#f2f2ee;border-radius:4px;padding:2px 6px;font-size:10px;color:#555">{a['source']}</span>
                        <span style="color:#bbb;font-size:10px;margin-left:4px">{a['cat']}</span>
                        <div style="font-weight:600;font-size:13px;margin:5px 0 3px">{a['title']}</div>
                        <div style="font-size:12px;color:#888;margin-bottom:4px">{a.get('summary','')[:180]}</div>
                        <a href="{a['url']}" target="_blank" style="font-size:11px;color:#185FA5">원문 →</a>
                        {f'<div style="font-size:11px;color:#777;background:#f8f8f6;border-radius:5px;padding:8px;margin-top:6px">{exp}</div>' if exp else ''}
                        </div>""", unsafe_allow_html=True)
                with sc:
                    st.write("")
                    if st.button("💾", key=f"s_{idx}", help="폴더에 저장"):
                        dlg_save_article(a)

    others = [(i, a) for i, a in enumerate(arts) if i not in clustered]
    if others:
        with st.expander(f"기타  ({len(others)}개)"):
            for i, a in others:
                oc, osc = st.columns([11, 1])
                with oc:
                    st.markdown(f"**[{a['title']}]({a['url']})**")
                    st.caption(a.get('summary', '')[:150])
                with osc:
                    if st.button("💾", key=f"os_{i}", help="폴더에 저장"):
                        dlg_save_article(a)

# ── BOOKMARK VIEW ─────────────────────────────────────────────────────────────

elif st.session_state.view == 'bookmarks':
    folder = st.session_state.sel_folder
    st.markdown(f"## {'📁 ' + folder['name'] if folder else '📁 전체 북마크'}")

    bms = get_bookmarks(folder['id'] if folder else None)

    if not bms:
        st.info("저장된 항목이 없어요. 리포트에서 💾 버튼으로 저장해보세요.")
    else:
        for bm in bms:
            with st.container(border=True):
                bc1, bc2 = st.columns([10, 1])
                with bc1:
                    if bm['type'] == 'report':
                        st.markdown(f"📌 **{bm['title']}**")
                        if bm.get('report_date'):
                            st.caption(f"📅 {bm['report_date']}")
                        if st.button("리포트 보기 →", key=f"go_{bm['id']}"):
                            st.session_state.update(view='report', sel_date=bm['report_date'])
                            st.query_params['date'] = bm['report_date']
                            st.rerun()
                    else:
                        src = bm.get('source', '')
                        url = bm.get('url', '')
                        title = bm.get('title', '')
                        st.markdown(
                            f"{'`'+src+'`  ' if src else ''}**{'['+title+']('+url+')' if url else title}**")
                        if bm.get('summary'):
                            st.caption(bm['summary'][:200])
                        if bm.get('report_date'):
                            st.caption(f"📅 {bm['report_date']}")
                        if bm.get('folders'):
                            st.caption(f"📁 {bm['folders']['name']}")
                with bc2:
                    if st.button("🗑", key=f"del_{bm['id']}", help="삭제"):
                        sb.table('bookmarks').delete().eq('id', bm['id']).execute()
                        get_bookmarks.clear()
                        st.rerun()
