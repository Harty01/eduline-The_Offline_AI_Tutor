# app.py
import streamlit as st
import pandas as pd
import random
import sqlite3
import json
import uuid
from datetime import datetime
from typing import Dict, List

# ==============================
# Config
# ==============================
st.set_page_config(
    page_title="EDULINE Adaptive Quiz",
    page_icon="logo_favicon1.png",
    layout="centered",
)

QUESTIONS_CSV = "questions_clus8.csv"   # your question file
DB_PATH = "eduline.db"
DEFAULT_TOTAL_Q = 5
CLUSTER_LIMITS = {"English": 7, "Mathematics": 8}
MIN_CLUSTER = 1

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

def insert_user(conn, student_uuid: str, name: str, area: str):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (student_uuid, name, area, created_at) VALUES (?, ?, ?, ?)
    """, (student_uuid, name, area, datetime.now().isoformat()))
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
    # Ensure Cluster is integer and Subject exists
    df["Cluster"] = df["Cluster"].astype(int)
    return df

df_all = load_questions(QUESTIONS_CSV)

# ==============================
# Session state init
# ==============================
if "app" not in st.session_state:
    st.session_state.app = {
        "stage": "register",   # register -> subject -> quiz -> finished
        "student_uuid": None,
        "name": "",
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
        "used_indices": [],      # store row indices used (integers)
        "current_question": None,
        "submitted": False,
        "feedback": "",
        "weak_clusters": {},     # {cluster_id: mistakes}
        "mode": "normal",        # normal | weak_only
        "weak_only_list": [],
        "cluster_name_map": {}   # user-provided topic names
    }

app = st.session_state.app
quiz = st.session_state.quiz

# ==============================
# Helpers: ID, quiz logic, cluster names
# ==============================
def gen_uuid() -> str:
    return "EDU-" + str(uuid.uuid4())[:8].upper()

def reset_quiz_state(subject: str, total_q: int, mode: str = "normal", weak_only_list: List[int] = None):
    mid = CLUSTER_LIMITS.get(subject, 6) // 2
    start_cluster = mid if mode == "normal" else (weak_only_list[0] if weak_only_list else mid)
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
        "weak_clusters": {} if mode == "normal" else {c:0 for c in (weak_only_list or [])},
        "mode": mode,
        "weak_only_list": weak_only_list or []
    })

def get_cluster_name(subject: str, cluster_id: int) -> str:
    # Use mapping in quiz["cluster_name_map"] if provided, else default friendly names
    mapping = quiz.get("cluster_name_map", {})
    key = f"{subject}_{cluster_id}"
    if key in mapping and mapping[key].strip():
        return mapping[key].strip()
    # default generic names by difficulty
    defaults = {
        1: "Foundations",
        2: "Basics",
        3: "Practice",
        4: "Intermediate",
        5: "Advanced Practice",
        6: "Advanced",
        7: "Expert",
        8: "Mastery"
    }
    return defaults.get(cluster_id, f"Cluster {cluster_id}")

def load_next_question():
    df_subj = df_all[df_all["Subject"] == quiz["subject"]].reset_index(drop=True)
    # if mode is weak_only, restrict cluster choices
    target_cluster = quiz["cluster"]
    if quiz["mode"] == "weak_only" and quiz["weak_only_list"]:
        if target_cluster not in quiz["weak_only_list"]:
            target_cluster = random.choice(quiz["weak_only_list"])
            quiz["cluster"] = target_cluster

    subset = df_subj[df_subj["Cluster"] == target_cluster].drop(quiz["used_indices"], errors="ignore")
    if subset.empty:
        # fallback to other weak clusters in weak_only mode, else any remaining
        if quiz["mode"] == "weak_only" and quiz["weak_only_list"]:
            subset = df_subj[df_subj["Cluster"].isin(quiz["weak_only_list"])].drop(quiz["used_indices"], errors="ignore")
        else:
            subset = df_subj.drop(quiz["used_indices"], errors="ignore")

    if subset.empty:
        return False

    q = subset.sample(1).iloc[0]
    quiz["current_question"] = q
    quiz["used_indices"].append(q.name)  # index in df_subj after reset_index
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
            quiz["cluster"] = min(CLUSTER_LIMITS.get(quiz["subject"], quiz["cluster"]+1), quiz["cluster"] + 1)
        quiz["feedback"] = "✅ Correct! Great job."
    else:
        if quiz["mode"] == "normal":
            quiz["cluster"] = max(MIN_CLUSTER, quiz["cluster"] - 1)
        quiz["feedback"] = f"❌ Wrong! Correct answer: {correct}"
        quiz["weak_clusters"][cluster_at_time] = quiz["weak_clusters"].get(cluster_at_time, 0) + 1
    quiz["submitted"] = True

def finish_and_record():
    # Save results to DB
    progress_ratio = quiz["question_index"] / max(1, quiz["total_questions"])
    try:
        save_result(conn, app["student_uuid"], quiz["subject"], quiz["score"], quiz["total_questions"], progress_ratio, quiz["weak_clusters"])
    except Exception as e:
        st.warning(f"Could not save results to DB: {e}")

    quiz["started"] = False
    app["stage"] = "finished"

# ==============================
# UI: Top header
# ==============================
st.title("🎓 EDULINE")
st.subheader("The Offline AI Tutor")

# Optional: show small student info summary on sidebar if registered
if app.get("student_uuid"):
    st.sidebar.markdown(f"**Student ID:** {app['student_uuid']}")
    if app.get("name"):
        st.sidebar.markdown(f"**Name:** {app['name']}")
    if app.get("area"):
        st.sidebar.markdown(f"**Area:** {app['area']}")
    # allow viewing past results
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
    with col2:
        area = st.radio("Where do you live?", ["Urban", "Rural"], index=0 if app.get("area","Urban") == "Urban" else 1)

    if st.button("Create Student ID"):
        student_uuid = gen_uuid()
        app["student_uuid"] = student_uuid
        app["name"] = name.strip()
        app["area"] = area
        # insert into DB
        try:
            insert_user(conn, student_uuid, app["name"], app["area"])
        except Exception as e:
            st.warning(f"Could not save registration: {e}")
        st.success(f"Registered! Your Student ID is: **{student_uuid}**")
        app["stage"] = "subject"
        st.rerun()

# ==============================
# STAGE: Subject selection & cluster-name overrides
# ==============================
elif app["stage"] == "subject":
    st.markdown(f"#### Welcome{', ' + app['name'] if app.get('name') else ''}! (ID: **{app['student_uuid']}**, Area: **{app['area']}**)")
    subject = st.selectbox("Select subject:", options=sorted(df_all["Subject"].unique()))
    total_q = st.slider("How many questions this round?", 3, 20, DEFAULT_TOTAL_Q)


    # show controls
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Start Adaptive Quiz"):
            # save mapping in session

            reset_quiz_state(subject, total_q, mode="normal")
            app["stage"] = "quiz"
            st.rerun()
    with col2:
        if st.button("Retry Weak Areas (if any)"):
            weak_list = [c for c, m in quiz["weak_clusters"].items() if m > 0]
            if not weak_list:
                st.info("No recorded weak areas yet. Try a normal quiz first.")
            else:
                # weak-only uses same subject
                quiz["cluster_name_map"].update(cluster_map_local)
                reset_quiz_state(subject, total_q, mode="weak_only", weak_only_list=weak_list)
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

    # load next question if needed
    if quiz["current_question"] is None:
        ok = load_next_question()
        if not ok:
            st.warning("No more questions available for this subject/mode.")
            finish_and_record()
            st.rerun()

    # Progress bar (no percentage text)
    progress_ratio = quiz["question_index"] / quiz["total_questions"]
    st.progress(progress_ratio)
    st.write(f"Question {quiz['question_index'] + 1} of {quiz['total_questions']}")

    q = quiz["current_question"]
    # Display friendly cluster name
    friendly_name = get_cluster_name(quiz["subject"], quiz["cluster"])
    st.info(f"Topic: **{friendly_name}** (Cluster {quiz['cluster']})")
    st.markdown(q["Question"])

    options = {"A": q["Option A"], "B": q["Option B"], "C": q["Option C"], "D": q["Option D"]}
    choice = st.radio("Choose answer:", options=list(options.keys()),
                      format_func=lambda k: f"{k}. {options[k]}",
                      key=f"choice_{quiz['question_index']}")

    col1, col2, col3 = st.columns([1,1,1])
    with col1:
        if st.button("Submit Answer"):
            submit_answer(choice)
            st.rerun()
    with col2:
        if st.button("Quit Quiz"):
            # save partial progress and finish
            finish_and_record()
            st.warning("You quit the quiz early. Your progress is saved.")
            st.rerun()
    with col3:
        if st.button("Restart Quiz"):
            # reset to subject selection stage but keep registration
            quiz.update({
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
                "weak_only_list": []
            })
            app["stage"] = "subject"
            st.rerun()

    # After submitting
    if quiz["submitted"]:
        if "✅" in quiz["feedback"]:
            st.success(quiz["feedback"])
        else:
            st.error(quiz["feedback"])

        # Next or finish
        if st.button("Next Question"):
            quiz["question_index"] += 1
            quiz["current_question"] = None
            quiz["submitted"] = False
            quiz["feedback"] = ""
            # If done
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
    st.progress(1.0)  # fully filled bar

    if quiz["weak_clusters"]:
        st.markdown("### ⚠ Weak Areas")
        for cl, misses in sorted(quiz["weak_clusters"].items(), key=lambda x: -x[1]):
            st.write(f"- {get_cluster_name(quiz['subject'], cl)} (Cluster {cl}): {misses} mistake(s)")

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
