# app.py
import streamlit as st
import pandas as pd
import random
import sqlite3
import json
import uuid
import os
from datetime import datetime
from typing import List
from streamlit_autorefresh import st_autorefresh

# ==============================
# Config
# ==============================
st.set_page_config(
    page_title="EDULINE Adaptive Quiz",
    page_icon="logo_favicon1.png",
    layout="centered",
)

BASE_DIR = os.path.dirname(__file__)
QUESTIONS_CSV = os.path.join(BASE_DIR, "questions_clus8.csv")
DB_PATH = "eduline.db"
DEFAULT_TOTAL_Q = 5
CLUSTER_LIMITS = {"English": 7, "Mathematics": 8}
MIN_CLUSTER = 1

# ==============================
# Cluster → Topic Mapping
# ==============================
cluster_topics = {
    0: {"English": "Verb Tenses, Sentence Completion", "Mathematics": "Basic Arithmetic, Word Problems"},
    1: {"English": "Spelling, Vocabulary", "Mathematics": "Number Properties"},
    2: {"English": "Grammar (Sentence Structure)", "Mathematics": "Equations, Expressions"},
    3: {"English": "Parts of Speech (Adjectives, Nouns)", "Mathematics": "Geometry Basics"},
    4: {"English": "Synonyms, Antonyms, Vocabulary", "Mathematics": "Basic Operations"},
    5: {"English": "Passive Voice, Opposites", "Mathematics": "Simple Calculations"},
    6: {"English": "Prepositions, Conjunctions", "Mathematics": "Fractions, Decimals"},
    7: {"English": "Minor Grammar", "Mathematics": "Ratios, Word Problems"},
    8: {"Mathematics": "Algebra, Fractions, Equations"}
}

# ==============================
# Database utils
# ==============================
def init_db(path=DB_PATH):
    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_uuid TEXT UNIQUE,
            name TEXT,
            phone TEXT,
            email TEXT,
            area TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_uuid TEXT,
            subject TEXT,
            score INTEGER,
            total_questions INTEGER,
            progress REAL,
            weak_clusters_json TEXT,
            taken_at TEXT
        )
    """)
    conn.commit()
    return conn

def insert_user(conn, student_uuid: str, name: str, phone: str, email: str, area: str):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (student_uuid, name, phone, email, area, created_at) VALUES (?, ?, ?, ?, ?, ?)
    """, (student_uuid, name, phone, email, area, datetime.now().isoformat()))
    conn.commit()

def save_result(conn, student_uuid: str, subject: str, score: int, total_questions: int, progress: float, weak_clusters: dict):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO results (student_uuid, subject, score, total_questions, progress, weak_clusters_json, taken_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (student_uuid, subject, score, total_questions, progress, json.dumps(weak_clusters), datetime.now().isoformat()))
    conn.commit()

# Initialize DB
conn = init_db(DB_PATH)

# ==============================
# Load questions
# ==============================
@st.cache_data
def load_questions(path):
    df = pd.read_csv(path)
    df["Cluster"] = df["Cluster"].astype(int)
    return df

df_all = load_questions(QUESTIONS_CSV)

# ==============================
# Session state init
# ==============================
if "app" not in st.session_state:
    st.session_state.app = {
        "stage": "register",
        "student_uuid": None,
        "name": "",
        "phone": "",
        "email": "",
        "area": "",
    }

if "quiz" not in st.session_state:
    st.session_state.quiz = {
        "started": False,
        "subject": None,
        "cluster": 0,
        "question_index": 0,
        "total_questions": DEFAULT_TOTAL_Q,
        "score": 0,
        "used_indices": [],
        "current_question": None,
        "submitted": False,
        "feedback": "",
        "weak_clusters": {},
        "mode": "normal",
        "weak_only_list": [],
        "cluster_name_map": {},
        "time_left": 0,
        "last_tick": None,
        "enable_timer": True
    }

app = st.session_state.app
quiz = st.session_state.quiz

# ==============================
# Helpers
# ==============================
def gen_uuid() -> str:
    return "EDU-" + str(uuid.uuid4())[:8].upper()

def reset_quiz_state(subject: str, total_q: int, mode: str = "normal", weak_only_list: List[int] = None, enable_timer=True):
    mid = CLUSTER_LIMITS.get(subject, 6) // 2
    start_cluster = mid if mode == "normal" else (weak_only_list[0] if weak_only_list else mid)
    total_time = total_q * 15  # 15 sec per Q, continuous
    quiz.update({
        "started": True,
        "subject": subject,
        "cluster": start_cluster,
        "question_index": 0,
        "total_questions": total_q,
        "score": 0,
        "used_indices": [],
        "current_question": None,
        "submitted": False,
        "feedback": "",
        "weak_clusters": {} if mode == "normal" else {c: 0 for c in (weak_only_list or [])},
        "mode": mode,
        "weak_only_list": weak_only_list or [],
        "time_left": total_time if enable_timer else 0,
        "last_tick": datetime.now(),
        "enable_timer": enable_timer
    })

def load_next_question():
    df_subj = df_all[df_all["Subject"] == quiz["subject"]].reset_index(drop=True)
    target_cluster = quiz["cluster"]
    if quiz["mode"] == "weak_only" and quiz["weak_only_list"]:
        if target_cluster not in quiz["weak_only_list"]:
            target_cluster = random.choice(quiz["weak_only_list"])
            quiz["cluster"] = target_cluster

    subset = df_subj[df_subj["Cluster"] == target_cluster].drop(quiz["used_indices"], errors="ignore")
    if subset.empty:
        if quiz["mode"] == "weak_only" and quiz["weak_only_list"]:
            subset = df_subj[df_subj["Cluster"].isin(quiz["weak_only_list"])].drop(quiz["used_indices"], errors="ignore")
        else:
            subset = df_subj.drop(quiz["used_indices"], errors="ignore")

    if subset.empty:
        return False

    q = subset.sample(1).iloc[0]
    quiz["current_question"] = q
    quiz["used_indices"].append(q.name)
    quiz["submitted"] = False
    quiz["feedback"] = ""
    return True

def submit_answer(choice_key: str):
    q = quiz["current_question"]
    correct = str(q["Correct Answer"]).strip().upper()
    cluster_at_time = quiz["cluster"]
    if choice_key == correct:
        quiz["score"] += 1
        if quiz["mode"] == "normal":
            quiz["cluster"] = min(CLUSTER_LIMITS.get(quiz["subject"], quiz["cluster"] + 1), quiz["cluster"] + 1)
        quiz["feedback"] = "✅ Correct! Great job."
    else:
        if quiz["mode"] == "normal":
            quiz["cluster"] = max(MIN_CLUSTER, quiz["cluster"] - 1)
        quiz["feedback"] = f"❌ Wrong! Correct answer: {correct}"
        quiz["weak_clusters"][cluster_at_time] = quiz["weak_clusters"].get(cluster_at_time, 0) + 1
    quiz["submitted"] = True

def finish_and_record():
    progress_ratio = quiz["question_index"] / max(1, quiz["total_questions"])
    # Map weak clusters to topic names
    mapped_weak = {}
    for cl, misses in quiz["weak_clusters"].items():
        subject = quiz["subject"]
        topic_name = cluster_topics.get(cl, {}).get(subject, f"Topic {cl}")
        mapped_weak[topic_name] = misses
    try:
        save_result(conn, app["student_uuid"], quiz["subject"], quiz["score"], quiz["total_questions"], progress_ratio, mapped_weak)
    except Exception as e:
        st.warning(f"Could not save results to DB: {e}")
    quiz["started"] = False
    app["stage"] = "finished"

# ==============================
# UI: Header
# ==============================
st.title("🎓 EDULINE")
st.subheader("The ADAPTIVE AI Tutor")

if app.get("student_uuid"):
    st.sidebar.markdown(f"**Student ID:** {app['student_uuid']}")
    if app.get("name"):
        st.sidebar.markdown(f"**Name:** {app['name']}")
    if app.get("phone"):
        st.sidebar.markdown(f"**Phone:** {app['phone']}")
    if app.get("email"):
        st.sidebar.markdown(f"**Email:** {app['email']}")
    if app.get("area"):
        st.sidebar.markdown(f"**Area:** {app['area']}")
    if st.sidebar.button("Show my past results"):
        try:
            df_results = pd.read_sql_query("SELECT * FROM results WHERE student_uuid=?", conn, params=(app['student_uuid'],))
            if df_results.empty:
                st.sidebar.info("No previous results found.")
            else:
                st.sidebar.dataframe(df_results.sort_values("taken_at", ascending=False).head(10))
        except Exception as e:
            st.sidebar.error(f"Failed to fetch results: {e}")

# ==============================
# STAGE: Register
# ==============================
if app["stage"] == "register":
    st.markdown("### 👋 Welcome! Create your student profile")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Name (optional)", value=app.get("name", ""))
        phone = st.text_input("Phone (optional)", value=app.get("phone", ""))
    with col2:
        email = st.text_input("Email (optional)", value=app.get("email", ""))
        area = st.radio("Where do you live?", ["Urban", "Rural"], index=0 if app.get("area","Urban") == "Urban" else 1)

    if st.button("Create Student ID"):
        student_uuid = gen_uuid()
        app.update({"student_uuid": student_uuid, "name": name.strip(), "phone": phone.strip(), "email": email.strip(), "area": area})
        try:
            insert_user(conn, student_uuid, app["name"], app["phone"], app["email"], app["area"])
        except Exception as e:
            st.warning(f"Could not save registration: {e}")
        st.success(f"Registered! Your Student ID is: **{student_uuid}**")
        app["stage"] = "subject"
        st.rerun()

# ==============================
# STAGE: Subject selection
# ==============================
elif app["stage"] == "subject":
    st.markdown(f"#### Welcome{', ' + app['name'] if app.get('name') else ''}! (ID: **{app['student_uuid']}**)")

    subject = st.selectbox("Select subject:", options=sorted(df_all["Subject"].unique()))
    total_q = st.slider("How many questions this round?", 3, 20, DEFAULT_TOTAL_Q)
    enable_timer = st.checkbox("Enable Timer?", True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Start Adaptive Quiz"):
            reset_quiz_state(subject, total_q, mode="normal", enable_timer=enable_timer)
            app["stage"] = "quiz"
            st.rerun()
    with col2:
        if st.button("Retry Weak Areas (if any)"):
            weak_list = [c for c, m in quiz["weak_clusters"].items() if m > 0]
            if not weak_list:
                st.info("No recorded weak areas yet. Try a normal quiz first.")
            else:
                reset_quiz_state(subject, total_q, mode="weak_only", weak_only_list=weak_list, enable_timer=enable_timer)
                app["stage"] = "quiz"
                st.rerun()

# ==============================
# STAGE: Quiz
# ==============================
elif app["stage"] == "quiz":
    if not quiz["started"]:
        st.warning("No active quiz. Returning to subject page.")
        app["stage"] = "subject"
        st.rerun()

    st.markdown(f"#### Subject: **{quiz['subject']}**")
    st.caption(f"Mode: {'Weak-only' if quiz['mode']=='weak_only' else 'Adaptive'} | Student ID: {app['student_uuid']}")

    if quiz["current_question"] is None:
        ok = load_next_question()
        if not ok:
            st.warning("No more questions available.")
            finish_and_record()
            st.rerun()

    # Continuous timer
    if quiz.get("enable_timer", False):
        st_autorefresh(interval=1000, key="timer_refresh")
        now = datetime.now()
        elapsed = (now - quiz.get("last_tick", now)).total_seconds()
        if elapsed >= 1:
            quiz["time_left"] -= int(elapsed)
            quiz["last_tick"] = now
        if quiz["time_left"] <= 0:
            st.warning("⏰ Time’s up! Auto-submitting your quiz...")
            finish_and_record()
            st.rerun()
        mins, secs = divmod(quiz["time_left"], 60)
        st.sidebar.markdown(f"⏳ **Time Left:** {mins:02d}:{secs:02d}")

    # Progress
    progress_ratio = quiz["question_index"] / quiz["total_questions"]
    st.progress(progress_ratio)
    st.write(f"Question {quiz['question_index'] + 1} of {quiz['total_questions']}")

    q = quiz["current_question"]
    #st.info(f"Topic: Question from your subject")
    st.markdown(q["Question"])

    options = {"A": q["Option A"], "B": q["Option B"], "C": q["Option C"], "D": q["Option D"]}
    choice = st.radio("Choose answer:", options=list(options.keys()), format_func=lambda k: f"{k}. {options[k]}", key=f"choice_{quiz['question_index']}")

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.button("Submit Answer"):
            submit_answer(choice)
            st.rerun()
    with col2:
        if st.button("Quit Quiz"):
            finish_and_record()
            st.warning("You quit the quiz early. Progress saved.")
            st.rerun()
    with col3:
        if st.button("Restart Quiz"):
            app["stage"] = "subject"
            quiz["started"] = False
            st.rerun()

    if quiz["submitted"]:
        if "✅" in quiz["feedback"]:
            st.success(quiz["feedback"])
        else:
            st.error(quiz["feedback"])
        if st.button("Next Question"):
            quiz["question_index"] += 1
            quiz["current_question"] = None
            quiz["submitted"] = False
            quiz["feedback"] = ""
            if quiz["question_index"] >= quiz["total_questions"]:
                finish_and_record()
            st.rerun()

# ==============================
# STAGE: Finished
# ==============================
elif app["stage"] == "finished":
    st.balloons()
    st.subheader("🎓 Quiz Completed!")
    st.write(f"**Final Score:** {quiz['score']} / {quiz['total_questions']}")
    st.progress(1.0)

    if quiz["weak_clusters"]:
        st.markdown("### ⚠ Weak Areas")
        for cl, misses in sorted(quiz["weak_clusters"].items(), key=lambda x: -x[1]):
            subject = quiz["subject"]
            topic_name = cluster_topics.get(cl, {}).get(subject, f"Topic {cl}")
            st.write(f"- **{topic_name}**: {misses} mistake(s)")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Retry Weak Areas"):
            weak_list = [c for c, m in quiz["weak_clusters"].items() if m > 0]
            if not weak_list:
                st.info("No weak areas recorded. Try a quiz first.")
            else:
                reset_quiz_state(quiz["subject"], quiz["total_questions"], mode="weak_only", weak_only_list=weak_list)
                app["stage"] = "quiz"
                st.rerun()
    with col2:
        if st.button("Choose Another Subject"):
            app["stage"] = "subject"
            st.rerun()
