import streamlit as st
import gspread
import pandas as pd
import json
import re
import ast
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- CONFIGURAZIONE ---
DEFAULT_MODEL = "gemini-1.5-flash" # Modello di fallback sicuro
PRO_MODEL = "gemini-3-pro-preview" # Il tuo modello richiesto

# --- INIZIALIZZAZIONE STATO ---
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'token_usage' not in st.session_state: 
    st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}

# --- 1. LOGIN ---
if not st.session_state['logged_in']:
    st.title("🦁 MasterTb Accesso")
    with st.form("login"):
        pwd = st.text_input("Password", type="password")
        if st.form_submit_button("Entra"):
            if "login_password" in st.secrets and pwd == st.secrets["login_password"]:
                st.session_state['logged_in'] = True
                st.rerun()
            else:
                st.error("Password errata")
    st.stop()

# --- 2. CONNESSIONE GOOGLE SHEET ---
@st.cache_resource
def get_worksheet():
    try:
        creds = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(creds)
        return gc.open("MasterTbGoogleAi").get_worksheet(0)
    except Exception as e:
        st.error(f"Errore connessione Sheet: {e}")
        st.stop()

ws = get_worksheet()

@st.cache_data(ttl=60)
def load_data():
    df = pd.DataFrame(ws.get_all_records())
    if not df.empty:
        df.columns = [c.strip() for c in df.columns]
        df.set_index(df.columns[0], inplace=True)
    return df

df = load_data()
if df.empty: st.stop()

# Dati di base
product_ids = [str(i) for i in df.index.tolist()]
cols = df.columns.tolist()
id_col = df.index.name

# --- 3. FUNZIONE AI (CON TOKEN COUNTER) ---
def search_with_gemini(query, dataframe, model_name):
    if "GOOGLE_API_KEY" not in st.secrets:
        st.error("Manca API Key")
        return []

    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    # Costruzione contesto ottimizzato (JSON leggero solo con colonne utili)
    # Prende indice (Nome) + Colonna 15 (Descrizione Breve se esiste, altrimenti tutto)
    try:
        target_col = dataframe.columns[15] if len(dataframe.columns) > 15 else dataframe.columns[0]
        context_data = dataframe[[target_col]].to_dict()[target_col] # Dizionario {Nome: Descrizione}
        context_str = json.dumps(context_data, ensure_ascii=False)
    except:
        context_str = dataframe.to_string() # Fallback

    sys_prompt = f"""Sei un esperto di team building. Cerca nel catalogo JSON fornito.
    CATALOGO: {context_str}
    
    Trovami i 'Nome Format' (chiavi del JSON) che meglio rispondono alla richiesta utente.
    Output: SOLO una lista Python di stringhe. Es: ["Format A", "Format B"].
    Se nulla corrisponde: []."""

    # Configurazione Modello (Il tuo codice esatto)
    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config={"temperature": 0.0},
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )

    try:
        response = model.generate_content(f"RICHIESTA UTENTE: {query}\n{sys_prompt}")
        
        # Conteggio Token
        if response.usage_metadata:
            st.session_state['token_usage']['input'] += response.usage_metadata.prompt_token_count
            st.session_state['token_usage']['output'] += response.usage_metadata.candidates_token_count
            st.session_state['token_usage']['total'] += response.usage_metadata.total_token_count

        # Parsing
        text = response.text.strip()
        match = re.search(r"(\[.*\])", text, re.DOTALL)
        return ast.literal_eval(match.group(1)) if match else []
        
    except Exception as e:
        st.error(f"Errore Gemini ({model_name}): {e}")
        return []

# --- INTERFACCIA ---
st.title("🦁 MasterTb Manager")

# Sidebar Counter
with st.sidebar:
    st.header("🔢 Token Sessione")
    st.metric("Input", st.session_state['token_usage']['input'])
    st.metric("Output", st.session_state['token_usage']['output'])
    st.metric("Totale", st.session_state['token_usage']['total'])
    if st.button("Reset Token"):
        st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}
        st.rerun()

tab1, tab2, tab3 = st.tabs(["👁️ Cerca & Modifica", "➕ Nuovo Format", "📄 Doc AI"])

# TAB 1: RICERCA
with tab1:
    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.subheader("Ricerca Intelligente")
    with col_b:
        # Selettore modello con 3.0 default
        modello_scelto = st.selectbox("Modello", [PRO_MODEL, DEFAULT_MODEL, "gemini-1.5-pro"], index=0)

    # FORM ANTI-LOOP
    with st.form("search_ai"):
        query = st.text_input("Cosa stai cercando?", placeholder="Es: attività all'aperto economiche")
        cerca_btn = st.form_submit_button("Cerca con Gemini 🦁")

    ids_to_show = product_ids
    
    if cerca_btn and query:
        with st.spinner(f"Chiedo a {modello_scelto}..."):
            res = search_with_gemini(query, df, modello_scelto)
            if res:
                ids_to_show = [x for x in res if x in product_ids]
                if not ids_to_show: st.warning("Trovati risultati ma non presenti nel DB attuale.")
            else:
                st.warning("Nessun risultato o errore API.")

    sel_id = st.selectbox(f"Seleziona {id_col}", ids_to_show)
    
    if sel_id:
        # MODIFICA
        row = df.loc[sel_id]
        with st.form("edit"):
            st.markdown(f"### Modifica: {sel_id}")
            new_vals = {}
            for c in cols:
                val = str(row[c])
                if len(val) > 60: new_vals[c] = st.text_area(c, val)
                else: new_vals[c] = st.text_input(c, val)
            
            if st.form_submit_button("Salva su Google Sheet 💾"):
                for c, v in new_vals.items():
                    if str(row[c]) != v:
                        r_idx = product_ids.index(sel_id) + 2 # +2 per header e 1-base
                        c_idx = cols.index(c) + 1
                        ws.update_cell(r_idx, c_idx, v)
                        st.toast(f"Aggiornato {c}")
                load_data.clear()
                st.rerun()

# TAB 2: AGGIUNTA
with tab2:
    st.header("Nuovo Format")
    with st.form("new"):
        d = {}
        for c in cols:
            d[c] = st.text_input(c) if c == id_col else st.text_area(c)
        
        if st.form_submit_button("Crea Riga"):
            if d[id_col] in product_ids:
                st.error("Esiste già!")
            else:
                ws.append_row([d[c] for c in cols])
                st.success("Fatto!")
                load_data.clear()
                st.rerun()

# TAB 3: DOCS
with tab3:
    st.info("Area caricamento PDF/PPT pronta per implementazione.")
