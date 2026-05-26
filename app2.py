import streamlit as st
import cv2
import mediapipe as mp
import numpy as np
import threading
import queue
import time
import pyttsx3
import pandas as pd
import plotly.express as px
from collections import deque
from streamlit_option_menu import option_menu
import tempfile
import os
# --- IMPORT DATABASE MANAGER ---
try:
    from db_manager import DatabaseManager
    db = DatabaseManager()
except ImportError:
    pass 

# ==========================================
# 1. BACKGROUND AUDIO ENGINE
# ==========================================
@st.cache_resource
def init_audio_engine():
    q = queue.Queue()
    def audio_worker(audio_q):
        import platform
        if platform.system() == "Windows":
            import pythoncom
            pythoncom.CoInitialize()
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            while True:
                try:
                    command = audio_q.get(block=True, timeout=1)
                    speaker.Speak(command)
                    audio_q.task_done()
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"[Audio Thread Error] {e}")
        else:
            engine = pyttsx3.init()
            engine.setProperty('rate', 160)
            while True:
                try:
                    command = audio_q.get(block=True, timeout=1)
                    engine.say(command)
                    engine.runAndWait()
                    audio_q.task_done()
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"[Audio Thread Error] {e}")
                
    threading.Thread(target=audio_worker, args=(q,), daemon=True).start()
    return q

audio_queue = init_audio_engine()

# ==========================================
# 1.5 OPTIMIZED THREADED CAMERA
# ==========================================
class ThreadedCamera:
    def __init__(self, src=0):
        self.capture = cv2.VideoCapture(src)
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.status, self.frame = self.capture.read()
        self.stopped = False

    def start(self):
        threading.Thread(target=self.update, args=(), daemon=True).start()
        return self

    def update(self):
        while not self.stopped:
            if self.capture.isOpened():
                self.capture.grab()
                self.status, self.frame = self.capture.retrieve()
            time.sleep(0.01)

    def read(self):
        return self.status, self.frame

    def stop(self):
        self.stopped = True
        if self.capture.isOpened():
            self.capture.release()

# ==========================================
# 2. STATE MACHINE LOGIC (Updated with your logic)
# ==========================================
class CricketRule:
    def evaluate(self, current_frame_data, frame_history):
        pass

class AdvancedChuckingDetector(CricketRule):
    def __init__(self):
        # Strict limit set to 30.0 degrees as per your logic
        self.max_extension_limit =25.0
        self.current_phase = 0
        
        self.min_angle_observed = 180.0
        self.max_extension_in_release = 0.0
        self.phase_2_violation = False
        self.phase_2_hand_x = None

        self.last_phase_change_time = 0.0
        self.timeout_duration = 5.0  
        
        self.cooldown_until = 0.0
        self.verdict_cooldown_duration = 5.0  

    def _calculate_joint_angle(self, shoulder, elbow, hand_base):
        """Calculates 3D joint angle at the elbow relative to the palm mass center."""
        p_shoulder = np.array(shoulder)
        p_elbow = np.array(elbow)
        p_hand = np.array(hand_base)

        vec_se = p_shoulder - p_elbow
        vec_he = p_hand - p_elbow  

        dot_product = np.dot(vec_se, vec_he)
        mag_se = np.linalg.norm(vec_se)
        mag_he = np.linalg.norm(vec_he)

        if mag_se == 0 or mag_he == 0:
            return 180.0

        cosine_angle = dot_product / (mag_se * mag_he)
        cosine_angle = np.clip(cosine_angle, -1.0, 1.0)
        return np.degrees(np.arccos(cosine_angle))

    def _reset_state_machine(self, print_reason=""):
        self.current_phase = 0
        self.min_angle_observed = 180.0
        self.max_extension_in_release = 0.0
        self.phase_2_violation = False
        self.phase_2_hand_x = None
        if print_reason:
            print(f"[STATE RESET] {print_reason}")

    def evaluate(self, current_frame_data, frame_history):
        # Safely extract the generic keys passed from the automated main pipeline
        shoulder = current_frame_data.get('shoulder')
        elbow = current_frame_data.get('elbow')
        hand_base = current_frame_data.get('hand_base')
        opp_shoulder = current_frame_data.get('opp_shoulder_ref')
        arm_label = current_frame_data.get('active_arm_label', 'Active')

        if not (shoulder and elbow and hand_base and opp_shoulder):
            return None

        current_time = time.time()

        # -----------------------------------------------------------------
        # COOLDOWN LOCKOUT LAYER
        # -----------------------------------------------------------------
        if current_time < self.cooldown_until:
            remaining = self.cooldown_until - current_time
            return f"COOLDOWN ACTIVE: Waiting {remaining:.1f}s"

        # -----------------------------------------------------------------
        # ACTIVE TIMEOUT ENFORCEMENT
        # -----------------------------------------------------------------
        if self.current_phase > 0:
            if (current_time - self.last_phase_change_time) > self.timeout_duration:
                self._reset_state_machine("5-second timeout reached. Action expired.")
                self.cooldown_until = current_time + 2.0 
                return "VERDICT: TIMEOUT - RESETTING"

        current_angle = self._calculate_joint_angle(shoulder, elbow, hand_base)
        
        y_w, y_e, y_s = hand_base[1], elbow[1], shoulder[1]
        x_w, x_e, x_s = hand_base[0], elbow[0], shoulder[0]
        x_opp = opp_shoulder[0]

        facing_direction = "LEFT" if x_s > x_opp else "RIGHT"

        # -----------------------------------------------------------------
        # PHASE 0 -> 1: LOAD-UP POSITION (WITH FRONT-SIDE FILTER)
        # -----------------------------------------------------------------
        if self.current_phase == 0:
            is_in_front = False
            if facing_direction == "LEFT" and x_w < x_s:
                is_in_front = True  
            elif facing_direction == "RIGHT" and x_w > x_s:
                is_in_front = True  

            if (y_w > y_e > y_s) and (150.0 < current_angle <= 180.0):
                if is_in_front:
                    return None  
                
                self.current_phase = 1
                self.last_phase_change_time = current_time  
                self.min_angle_observed = 180.0
                self.max_extension_in_release = 0.0
                self.phase_2_violation = False
                print(f"[STATE] Phase 1 Engaged: Back swing load-up confirmed (Facing: {facing_direction})")
                return "PHASE_1: LOAD_UP"

        # -----------------------------------------------------------------
        # PHASE 1 -> 2: HORIZONTAL RELEASE LEVEL
        # -----------------------------------------------------------------
        elif self.current_phase == 1:
            vertical_alignment = abs(y_w - y_s) < 0.15 and abs(y_e - y_s) < 0.15
            if vertical_alignment and (150.0 < current_angle <= 180.0):
                self.current_phase = 2
                self.last_phase_change_time = current_time  
                self.phase_2_hand_x = x_w  
                print("[STATE] Phase 2 Engaged: Arm at release plane. Strict chuck check active.")
                return "PHASE_2: RELEASE_LEVEL"

        # -----------------------------------------------------------------
        # PHASE 2 -> 3: HIGH FOLLOW-THROUGH
        # -----------------------------------------------------------------
        elif self.current_phase == 2:
            if current_angle < self.min_angle_observed:
                self.min_angle_observed = current_angle
            
            extension = current_angle - self.min_angle_observed
            self.max_extension_in_release = max(self.max_extension_in_release, extension)

            if extension > self.max_extension_limit:
                self.phase_2_violation = True

            if y_w < y_e < y_s:
                self.current_phase = 3
                self.last_phase_change_time = current_time  
                print("[STATE] Phase 3 Engaged: High follow-through tracking.")
                return "PHASE_3: HIGH_FOLLOW"

        # -----------------------------------------------------------------
        # PHASE 3 -> 0: X-INVERSION & VERDICT OUTPUT
        # -----------------------------------------------------------------
        elif self.current_phase == 3:
            has_inverted = False
            if self.phase_2_hand_x is not None:
                if (self.phase_2_hand_x < x_s and x_w > x_s) or (self.phase_2_hand_x > x_s and x_w < x_s):
                    has_inverted = True

            if y_w > y_s:
                was_illegal = self.phase_2_violation
                final_ext = self.max_extension_in_release
                self._reset_state_machine()  
                
                self.cooldown_until = current_time + self.verdict_cooldown_duration
                
                # We append :{final_ext:.2f} so the UI dashboard can still read the angle value
                if has_inverted:
                    if was_illegal:
                        return f"VERDICT: ILLEGAL DELIVERY (CHUCK DETECTED on {arm_label} Arm):{final_ext:.2f}"
                    else:
                        return f"VERDICT: PERFECT LEGAL DELIVERY:{final_ext:.2f}"
                else:
                    print("[STATE] Aborted: Delivery failed horizontal arc cross-over rules.")
                    return "VERDICT: INVALID ACTION:0.00"

        return None

class RulesEngine:
    def __init__(self):
        self.active_rules = [AdvancedChuckingDetector()]
        self.history = deque(maxlen=15)
        
    def process_frame(self, frame_data):
        self.history.append(frame_data)
        detected_flaws = []
        for rule in self.active_rules:
            flaw = rule.evaluate(frame_data, self.history)
            if flaw:
                detected_flaws.append(flaw)
        return detected_flaws

# ==========================================
# 3. ENHANCED STREAMLIT UI SETUP 
# ==========================================
st.set_page_config(page_title="UnSAFAL Log", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    
    .stApp { 
        background-color: #0E1117; 
        font-family: 'Inter', sans-serif;
    }
    
    .dashboard-card { 
        background: linear-gradient(145deg, #1A1C23, #15171C);
        border-radius: 16px; 
        padding: 24px; 
        border: 1px solid rgba(255, 255, 255, 0.05); 
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.2);
        margin-bottom: 24px; 
        transition: transform 0.2s ease;
    }
    .dashboard-card:hover {
        transform: translateY(-2px);
    }
    
    .alert-box-legal { background: rgba(75, 255, 122, 0.1); color: #4bff7a; border: 1px solid rgba(75, 255, 122, 0.3); padding: 16px; border-radius: 12px; text-align: center; font-weight: 600; font-size: 1.1rem; }
    .alert-box-illegal { background: rgba(255, 75, 75, 0.1); color: #ff4b4b; border: 1px solid rgba(255, 75, 75, 0.3); padding: 16px; border-radius: 12px; text-align: center; font-weight: 600; font-size: 1.1rem; }
    .alert-box-neutral { background: rgba(255, 255, 255, 0.02); color: #888; border: 1px dashed rgba(255, 255, 255, 0.1); padding: 16px; border-radius: 12px; text-align: center; font-weight: 600; font-size: 1.1rem; }
    
    .main-title { color: #F0F2F6; font-weight: 800; font-size: 2.2rem; margin-bottom: 0.2rem; }
    .sub-title { color: #8B949E; font-size: 1rem; margin-top: 0; margin-bottom: 30px; font-weight: 400;}
    
    .hero-logo { font-size: 5.5rem; font-weight: 800; text-align: center; color: #ffffff; padding-top: 60px; letter-spacing: -2px;}
    .hero-logo span { color: #00a8ff; background: -webkit-linear-gradient(#00a8ff, #0072ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent;}
    .hero-subtitle { font-size: 1.4rem; text-align: center; color: #8B949E; margin-top: -15px; margin-bottom: 60px; font-weight: 400;}
    
    .feature-box { background: rgba(255,255,255,0.02); padding: 35px 25px; border-radius: 16px; border: 1px solid rgba(255,255,255,0.05); text-align: center; height: 100%; transition: all 0.3s ease;}
    .feature-box:hover { background: rgba(255,255,255,0.05); border-color: rgba(0, 168, 255, 0.3); box-shadow: 0 10px 30px -10px rgba(0, 168, 255, 0.1); }
    .feature-icon { font-size: 3.5rem; margin-bottom: 20px; text-shadow: 0 4px 15px rgba(0,0,0,0.3);}
    
    div.stButton > button:first-child { background: linear-gradient(135deg, #00a8ff, #0072ff); color: white; border: none; font-weight: 600; border-radius: 8px; padding: 0.5rem 1rem;}
    div.stButton > button:first-child:hover { box-shadow: 0 4px 15px rgba(0, 168, 255, 0.4); border: none; color: white;}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("<h3 style='text-align: left; color: #F0F2F6; font-weight: 800; letter-spacing: -0.5px;'>🏏 PLAYER 1</h3>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    
    if 'current_page' not in st.session_state: st.session_state['current_page'] = "Home"
    menu_options = ["Home", "Player Stats", "Session History", "Dashboard"]
    default_idx = menu_options.index(st.session_state['current_page'])
    
    selected_nav = option_menu(
        menu_title=None, 
        options=menu_options, 
        icons=["house-door", "graph-up", "clock-history", "camera-video"], 
        menu_icon="cast", 
        default_index=default_idx, 
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"}, 
            "icon": {"color": "#8B949E", "font-size": "1.1rem"}, 
            "nav-link": {"font-size": "0.95rem", "text-align": "left", "margin":"4px 0px", "color": "#8B949E", "font-weight": "500", "border-radius": "8px"}, 
            "nav-link-selected": {"background": "linear-gradient(135deg, #00a8ff, #0072ff)", "color": "white", "font-weight": "600"}
        }
    )
    
    if selected_nav != st.session_state['current_page']:
        st.session_state['current_page'] = selected_nav
        st.rerun()
    
    st.markdown("---")
    
    run_camera = False
    uploaded_video = None
    
    if st.session_state['current_page'] == "Dashboard":
        st.markdown("<h4 style='color: #F0F2F6;'>🎥 Input Source</h4>", unsafe_allow_html=True)
        
        # 1. Live Camera
        run_camera = st.toggle("🔴 Live Camera", value=False)
        
        st.markdown("<p style='text-align: center; color: #8B949E; margin: 10px 0;'>— OR —</p>", unsafe_allow_html=True)
        
        # 2. File Upload
        uploaded_video = st.file_uploader("📂 Upload Video", type=['mp4', 'mov', 'avi'])
        
        st.markdown("---")
        if st.button("🔄 Reset Live Session", type="secondary", use_container_width=True):
            st.session_state['sess_total'] = 0
            st.session_state['sess_legal'] = 0
            st.session_state['sess_max_release'] = 0.0
            st.session_state['last_release_val'] = 0.0
            st.rerun()

# ==========================================
# 4. PAGE RENDERING
# ==========================================
if st.session_state['current_page'] == "Home":
    st.markdown("<div class='hero-logo'>Cric<span>GURU</span></div>", unsafe_allow_html=True)
    st.markdown("<div class='hero-subtitle'>Your Personal AI Cricket Coach</div>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        if st.button("🚀 Launch Live Dashboard", type="primary", use_container_width=True):
            st.session_state['current_page'] = "Dashboard"
            st.rerun()
            
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    
    f1, f2, f3 = st.columns(3)
    with f1: st.markdown("<div class='feature-box'><div class='feature-icon'>📐</div><h3 style='font-size:1.2rem;'>Biomechanical Analysis</h3><p style='color: #8B949E; font-size: 0.9rem;'>Real-time MediaPipe pose estimation.</p></div>", unsafe_allow_html=True)
    with f2: st.markdown("<div class='feature-box'><div class='feature-icon'>🚨</div><h3 style='font-size:1.2rem;'>Chucking Detection</h3><p style='color: #8B949E; font-size: 0.9rem;'>Strict 30° elbow extension rules engine.</p></div>", unsafe_allow_html=True)
    with f3: st.markdown("<div class='feature-box'><div class='feature-icon'>🎙️</div><h3 style='font-size:1.2rem;'>Live Audio Feedback</h3><p style='color: #8B949E; font-size: 0.9rem;'>Keep eyes on the pitch. Verbal coaching instantly.</p></div>", unsafe_allow_html=True)

# ------------------------------------------
# PAGE: PLAYER STATS
# ------------------------------------------
elif st.session_state['current_page'] == "Player Stats":
    st.markdown("<h1 class='main-title'>📈 Player Analytics</h1>", unsafe_allow_html=True)
    st.markdown("<p class='sub-title'>Review historical bowling trends and elbow extensions.</p>", unsafe_allow_html=True)

    if 'db' in globals():
        df = db.get_sessions_dataframe()
        if df.empty:
            st.info("No session data found yet! Go to the Dashboard and record a live session.")
        else:
            total_bowls = df["total_deliveries"].sum()
            total_legal = df.get("legal_deliveries", pd.Series([0]*len(df))).sum()
            max_release_overall = df.get("max_extension", pd.Series([0])).max() 
            overall_consistency = (total_legal / total_bowls) * 100 if total_bowls > 0 else 0

            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"""
                <div class="dashboard-card" style="text-align: center;">
                    <h5 style='color: #8B949E; margin-bottom: 8px; font-weight: 500;'>🎯 Consistency</h5>
                    <h1 style='color: #00a8ff; margin-top: 0; font-size: 3rem;'>{overall_consistency:.1f}%</h1>
                    <p style='color: #555; font-size: 0.85rem;'>{total_legal} Valid / {total_bowls} Total</p>
                </div>
                """, unsafe_allow_html=True)
                
            with col2:
                st.markdown(f"""
                <div class="dashboard-card" style="text-align: center;">
                    <h5 style='color: #8B949E; margin-bottom: 8px; font-weight: 500;'>🥎 Total Deliveries</h5>
                    <h1 style='color: #F0F2F6; margin-top: 0; font-size: 3rem;'>{total_bowls}</h1>
                    <p style='color: #555; font-size: 0.85rem;'>Across all tracked sessions</p>
                </div>
                """, unsafe_allow_html=True)

            with col3:
                st.markdown(f"""
                <div class="dashboard-card" style="text-align: center;">
                    <h5 style='color: #8B949E; margin-bottom: 8px; font-weight: 500;'>📏 Highest Extension</h5>
                    <h1 style='color: #ff4b4b; margin-top: 0; font-size: 3rem;'>{max_release_overall:.1f}°</h1>
                    <p style='color: #555; font-size: 0.85rem;'>Maximum recorded elbow flex</p>
                </div>
                """, unsafe_allow_html=True)

            # --- CLEAN BIOMECHANICS GRAPH ---
            st.markdown("<h4 style='margin-top:20px; color:#F0F2F6;'>📊 Peak Extension Over Time</h4>", unsafe_allow_html=True)
            
            fig = px.line(
                df, x="session_date", y="max_extension", markers=True,
                color_discrete_sequence=["#00a8ff"]
            )
            
            # The 30-Degree Threshold Line
            fig.add_hline(
                y=30.0, line_dash="dash", line_color="rgba(255, 75, 75, 0.8)", 
                annotation_text="Strict Chucking Limit (15°)", annotation_position="top left",
                annotation_font_color="#ff4b4b"
            )
            
            # Formatting the markers and hover tooltips
            fig.update_traces(
                line=dict(width=3), 
                marker=dict(size=8, symbol="circle", color="#0E1117", line=dict(color="#00a8ff", width=2)),
                hovertemplate="<b>Date:</b> %{x}<br><b>Max Extension:</b> %{y:.1f}°<extra></extra>"
            )
            
            # Layout & Styling Polish
            fig.update_layout(
                plot_bgcolor='rgba(0,0,0,0)', 
                paper_bgcolor='rgba(0,0,0,0)', 
                font_color='#8B949E',
                hovermode="x unified",
                margin=dict(l=0, r=0, t=30, b=0), 
                showlegend=False
            )
            
            # Configure the axes
            fig.update_xaxes(showgrid=False, title=None)
            fig.update_yaxes(title_text="Peak Angle (°)", showgrid=True, gridwidth=1, gridcolor='rgba(255,255,255,0.05)')
            
            st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------
# PAGE: SESSION HISTORY
# ------------------------------------------
elif st.session_state['current_page'] == "Session History":
    
    col_title, col_btn = st.columns([3, 1])
    
    with col_title:
        st.markdown("<h1 class='main-title'>🕒 Session Logs</h1>", unsafe_allow_html=True)
        st.markdown("<p class='sub-title'>Review your past coaching sessions.</p>", unsafe_allow_html=True)
        
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True) 
        if st.button("🗑️ Clear All History", type="primary", use_container_width=True):
            if 'db' in globals():
                db.clear_all_sessions()
                st.toast("Session history wiped clean!", icon="✅")
                time.sleep(0.5)
                st.rerun()

    if 'db' in globals():
        sessions = db.get_all_sessions()
        if not sessions:
            st.info("No sessions recorded yet. Start a live session to log data!")
        else:
            for s in sessions:
                total = s.get('total_deliveries', 0)
                legal = s.get('legal_deliveries', 0)
                val = s.get('max_extension', 0.0)
                
                legal_pct = (legal / total) * 100 if total > 0 else 0
                
                card_html = f"""
                <div class="dashboard-card" style="padding: 16px 24px; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between;">
                    <div style="flex: 1.5;">
                        <h5 style='color: #00a8ff; margin: 0; padding-bottom: 4px; font-size:1.1rem;'>{s['session_date']}</h5>
                        <span style='color: #8B949E; font-size: 0.85rem;'>⏱️ {s['session_time']}</span>
                    </div>
                    <div style="flex: 1; text-align: center;">
                        <p style='margin: 0; color:#ccc; font-size: 0.95rem;'>🥎 <b>{total}</b> Deliveries</p>
                    </div>
                    <div style="flex: 1; text-align: center;">
                        <p style='margin: 0; color:#4bff7a; font-size: 0.95rem;'>✅ <b>{legal_pct:.0f}%</b> Valid</p>
                    </div>
                    <div style="flex: 1; text-align: right;">
                        <p style='margin: 0; color:#F0F2F6; font-size: 0.95rem;'>Max Ext: <b>{val:.1f}°</b></p>
                    </div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)

# ------------------------------------------
# PAGE: DASHBOARD (LIVE DATA LOGGING)
# ------------------------------------------
# ------------------------------------------
# PAGE: DASHBOARD (LIVE & UPLOAD DATA LOGGING)
# ------------------------------------------
elif st.session_state['current_page'] == "Dashboard":
    st.markdown("<h1 class='main-title'>Video Analysis</h1>", unsafe_allow_html=True)
    st.markdown("<p class='sub-title'>Ensure your entire body is visible in the frame (Feet to Hands).</p>", unsafe_allow_html=True)

    # Determine if we should be running an analysis loop
    is_analyzing = run_camera or (uploaded_video is not None)

    if 'is_recording' not in st.session_state:
        st.session_state['is_recording'] = False
        st.session_state['sess_total'] = 0
        st.session_state['sess_legal'] = 0
        st.session_state['sess_max_release'] = 0.0

    if is_analyzing and not st.session_state['is_recording']:
        st.session_state['is_recording'] = True
        st.session_state['sess_total'], st.session_state['sess_max_release'] = 0, 0.0
        st.session_state['sess_legal'] = 0
        st.session_state['last_release_val'] = 0.0  

    if not is_analyzing and st.session_state['is_recording']:
        st.session_state['is_recording'] = False
        if st.session_state['sess_total'] > 0 and 'db' in globals():
            db.log_session(st.session_state['sess_total'], st.session_state['sess_legal'], st.session_state['sess_max_release'])
            st.toast(f"Session Saved! ({st.session_state['sess_total']} deliveries)", icon="✅")
        elif st.session_state['sess_total'] == 0:
            st.toast("Session ended. No deliveries recorded.", icon="⚠️")

    col_video, col_stats = st.columns([2.2, 1.0])
    
    with col_video:
        stframe = st.empty()
        
        if not is_analyzing:
            stframe.markdown("""
                <div class="dashboard-card" style="padding: 12px; min-height: 480px; display: flex; flex-direction: column; justify-content: center; align-items: center;">
                    <div style='text-align: center; color: #555;'>
                        <h1 style='font-size: 5rem; margin-bottom: 0;'>📷</h1>
                        <p style='font-size: 1.2rem;'>Camera Offline</p>
                        <p style='font-size: 0.9rem; color: #8B949E;'>Toggle the camera or upload a video in the sidebar.</p>
                    </div>
                </div>
            """, unsafe_allow_html=True)

    with col_stats:
        stat_card = st.empty()
        phase_card = st.empty()
        verdict_card = st.empty()

        stat_card.markdown(f"""
            <div class="dashboard-card" style="display: flex; justify-content: space-between;">
                <div>
                    <h4 style='margin-top:0; color:#F0F2F6; font-size:0.9rem;'>Total Tracked</h4>
                    <h2 style='color:#00a8ff; margin:0; font-size: 2.2rem;'>{st.session_state['sess_total']}</h2>
                </div>
                <div style="text-align: right;">
                    <h4 style='margin-top:0; color:#F0F2F6; font-size:0.9rem;'>Valid</h4>
                    <h2 style='color:#4bff7a; margin:0; font-size: 2.2rem;'>{st.session_state['sess_legal']}</h2>
                </div>
            </div>
        """, unsafe_allow_html=True)

        phase_card.markdown("""
            <div class="dashboard-card">
                <h4 style='margin-top:0; color:#F0F2F6; font-size:1rem;'>State Machine</h4>
                <div style='background: rgba(255,255,255,0.05); padding: 12px; border-radius: 8px; color: #00a8ff;'>Awaiting motion...</div>
            </div>
        """, unsafe_allow_html=True)

        verdict_card.markdown("""
            <div class="dashboard-card">
                <h4 style='margin-top:0; color:#F0F2F6; font-size:1rem;'>Live Extension Angle</h4>
                <div class='alert-box-neutral'>AWAITING DELIVERY</div>
            </div>
        """, unsafe_allow_html=True)

    mp_pose = mp.solutions.pose
    mp_drawing = mp.solutions.drawing_utils

    if is_analyzing:
        engine = RulesEngine()
        audio_queue.put("Analysis started.")
        
        # --- VIDEO SOURCE LOGIC ---
        temp_file_path = None
        if run_camera:
            cap = ThreadedCamera(0).start()
            is_live_feed = True
        elif uploaded_video is not None:
            # Save the uploaded file to a temporary location
            tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
            tfile.write(uploaded_video.read())
            temp_file_path = tfile.name
            
            # Standard OpenCV capture for files (no frames dropped)
            cap = cv2.VideoCapture(temp_file_path)
            is_live_feed = False
            
            # Reset the uploader state so it doesn't loop infinitely
            uploaded_video = None 
        
        last_verdict_ui, last_phase_ui = "", ""
        last_ui_update_time = time.time()
        ui_update_interval = 1.0 / 30.0 # Faster UI updates for recorded video  

        with mp_pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5) as pose:
            
            # The Main Video Loop
            while is_analyzing:
                if is_live_feed:
                    if cap.stopped: break
                    ret, frame = cap.read()
                else:
                    ret, frame = cap.read()
                    if not ret: 
                        st.toast("Video processing complete!", icon="✅")
                        break # End of uploaded video
                
                if not ret: break
                
                # Only flip the frame if it's a live front-facing camera
                if is_live_feed:
                    frame = cv2.flip(frame, 1)
                    
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = pose.process(rgb_frame)

                persistent_verdict = last_verdict_ui
                current_phase_status = last_phase_ui
                current_release_val = 0.0

                if results.pose_landmarks:
                    mp_drawing.draw_landmarks(rgb_frame, results.pose_landmarks, mp_pose.POSE_CONNECTIONS)
                    landmarks = results.pose_landmarks.landmark
                    
                    r_shoulder_vis = landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value].visibility
                    l_shoulder_vis = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value].visibility
                    active_arm = 'right' if r_shoulder_vis > l_shoulder_vis else 'left'

                    r_shoulder = landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
                    l_shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
                    r_elbow = landmarks[mp_pose.PoseLandmark.RIGHT_ELBOW.value]
                    l_elbow = landmarks[mp_pose.PoseLandmark.LEFT_ELBOW.value]
                    r_pinky = landmarks[mp_pose.PoseLandmark.RIGHT_PINKY.value]
                    r_index = landmarks[mp_pose.PoseLandmark.RIGHT_INDEX.value]
                    l_pinky = landmarks[mp_pose.PoseLandmark.LEFT_PINKY.value]
                    l_index = landmarks[mp_pose.PoseLandmark.LEFT_INDEX.value]
                    
                    r_hand_base = ((r_pinky.x + r_index.x) / 2.0, (r_pinky.y + r_index.y) / 2.0, (r_pinky.z + r_index.z) / 2.0)
                    l_hand_base = ((l_pinky.x + l_index.x) / 2.0, (l_pinky.y + l_index.y) / 2.0, (l_pinky.z + l_index.z) / 2.0)
                    r_shoulder_tuple = (r_shoulder.x, r_shoulder.y, r_shoulder.z)
                    l_shoulder_tuple = (l_shoulder.x, l_shoulder.y, l_shoulder.z)
                    r_elbow_tuple = (r_elbow.x, r_elbow.y, r_elbow.z)
                    l_elbow_tuple = (l_elbow.x, l_elbow.y, l_elbow.z)

                    if active_arm == 'right':
                        landmarks_dict = {
                            'shoulder': r_shoulder_tuple,
                            'elbow': r_elbow_tuple,
                            'hand_base': r_hand_base,
                            'opp_shoulder_ref': l_shoulder_tuple,
                            'active_arm_label': 'Right'
                        }
                    else:
                        landmarks_dict = {
                            'shoulder': l_shoulder_tuple,
                            'elbow': l_elbow_tuple,
                            'hand_base': l_hand_base,
                            'opp_shoulder_ref': r_shoulder_tuple,
                            'active_arm_label': 'Left'
                        }

                    engine_outputs = engine.process_frame(landmarks_dict)

                    if engine_outputs:
                        for output in engine_outputs:
                            if "VERDICT" in output:
                                persistent_verdict = output
                                
                                if "DELIVERY" in output and persistent_verdict != last_verdict_ui:
                                    st.session_state['sess_total'] += 1
                                    
                                    try:
                                        current_release_val = float(output.split(":")[-1])
                                    except:
                                        current_release_val = 0.0
                                    
                                    st.session_state['last_release_val'] = current_release_val
                                    st.session_state['sess_max_release'] = max(st.session_state['sess_max_release'], current_release_val)
                                    
                                    if "ILLEGAL" in output:
                                        audio_queue.put("No ball. Chucking detected.")
                                    else:
                                        st.session_state['sess_legal'] += 1
                                        audio_queue.put("Valid.")
                            else:
                                current_phase_status = output
                                if "PHASE_1" in output or "COOLDOWN ACTIVE" in output:
                                    persistent_verdict = "Tracking new delivery..."

                current_time = time.time()
                if (current_time - last_ui_update_time) >= ui_update_interval:
                    
                    stat_card.markdown(f"""
                        <div class="dashboard-card" style="display: flex; justify-content: space-between;">
                            <div>
                                <h4 style='margin-top:0; color:#F0F2F6; font-size:0.9rem;'>Total Tracked</h4>
                                <h2 style='color:#00a8ff; margin:0; font-size: 2.2rem;'>{st.session_state['sess_total']}</h2>
                            </div>
                            <div style="text-align: right;">
                                <h4 style='margin-top:0; color:#F0F2F6; font-size:0.9rem;'>Valid</h4>
                                <h2 style='color:#4bff7a; margin:0; font-size: 2.2rem;'>{st.session_state['sess_legal']}</h2>
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

                    if persistent_verdict != last_verdict_ui:
                        display_release_val = st.session_state.get('last_release_val', 0.0)
                        verdict_html = "<div class='alert-box-neutral'>AWAITING DELIVERY</div>"
                        
                        if "DELIVERY" in persistent_verdict:
                            if "ILLEGAL" in persistent_verdict:
                                verdict_html = f"<div class='alert-box-illegal'>🚨 ILLEGAL — EXT: {display_release_val:.1f}°</div>"
                            else:
                                verdict_html = f"<div class='alert-box-legal'>✅ LEGAL — EXT: {display_release_val:.1f}°</div>"
                        elif "Tracking" in persistent_verdict: 
                            verdict_html = "<div class='alert-box-neutral'>🏏 TRACKING MOTION...</div>"
                        elif "INVALID ACTION" in persistent_verdict:
                            verdict_html = "<div class='alert-box-neutral'>⚠️ INVALID BOWLING MOTION</div>"
                        
                        verdict_card.markdown(f"""
                            <div class="dashboard-card">
                                <h4 style='margin-top:0; color:#F0F2F6; font-size:1rem;'>Live Extension Angle</h4>
                                {verdict_html}
                            </div>
                        """, unsafe_allow_html=True)
                        last_verdict_ui = persistent_verdict
                        
                    if current_phase_status != last_phase_ui:
                        phase_card.markdown(f"""
                            <div class="dashboard-card">
                                <h4 style='margin-top:0; color:#F0F2F6; font-size:1rem;'>State Machine</h4>
                                <div style='background: rgba(255,255,255,0.05); padding: 12px; border-radius: 8px; color: #00a8ff;'>🔄 {current_phase_status}</div>
                            </div>
                        """, unsafe_allow_html=True)
                        last_phase_ui = current_phase_status

                    stframe.image(rgb_frame, channels="RGB", use_container_width=True)
                    last_ui_update_time = current_time
                    
                # Small sleep for pre-recorded video so it doesn't process instantly and look like a blur
                if not is_live_feed:
                    time.sleep(0.02)

        # Cleanup process
        if is_live_feed:
            cap.stop()
        else:
            cap.release()
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path) # Delete the temp file to save space
                
        audio_queue.put("Analysis ended.")
        stframe.empty()
    