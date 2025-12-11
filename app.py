import streamlit as st
import gspread
import pandas as pd
import json
import re
import ast
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- CONFIGURAZIONE ---
# Default richiesto (con prefisso 'models/' per compatibilità SDK)
DEFAULT_MODEL = "models/gemini-3-pro-preview" 

# --- INIZIALIZZAZIONE STATO ---
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'token_usage' not in st.session_state: 
    st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}
# Stato per la lista modelli (parte con il default, poi si aggiorna con la scansione)
if 'available_models' not in st.session_state:
    st.session_state['available_models'] = [DEFAULT_MODEL]

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
ws = None
@st.cache_resource
def connect_to_sheet():
    try:
        creds = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(creds)
        return gc.open("MasterTbGoogleAi").get_worksheet(0)
    except Exception as e:
        st.error(f"Errore Sheet: {e}")
        st.stop()

ws = connect_to_sheet()

@st.cache_data(ttl=60)
def load_data():
    df = pd.DataFrame(ws.get_all_records())
    if not df.empty:
        df.columns = [c.strip() for c in df.columns]
        df.set_index(df.columns[0], inplace=True)
    return df

df = load_data()
if df.empty: st.stop()

product_ids = [str(i) for i in df.index.tolist()]
cols = df.columns.tolist()
id_col = df.index.name

# --- 3. FUNZIONE AI ---
def search_ai(query, dataframe, model_name):
    if "GOOGLE_API_KEY" not in st.secrets:
        st.error("Manca API Key")
        return []

    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    # Preparazione Dati (Col 1 + Col 16)
    all_cols = list(dataframe.columns)
    target_col = all_cols[15] if len(all_cols) > 15 else all_cols[0]
    
    sub_df = dataframe[[target_col]]
    context_str = sub_df.to_markdown(index=True)

    sys_prompt = """
    Sei un assistente di ricerca specializzato in format di team building.
    Analizza la richiesta e il catalogo fornito (Nome Format + Descrizione).
    Output: SOLO una lista Python di stringhe dei Nomi Format trovati. Es: ['Format A', 'Format B'].
    Se nulla corrisponde: [].
    """

    # Configurazione Modello (Tuo codice esatto)
    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config={"temperature": 0.0},
        system_instruction=sys_prompt,
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )

    full_prompt = f"CATALOGO:\n{context_str}\n\nRICHIESTA UTENTE: {query}"
    
    try:
        response = model.generate_content(full_prompt)
        
        # Conteggio Token
        if response.usage_metadata:
            st.session_state['token_usage']['input'] += response.usage_metadata.prompt_token_count
            st.session_state['token_usage']['output'] += response.usage_metadata.candidates_token_count
            st.session_state['token_usage']['total'] += response.usage_metadata.total_token_count

        text = response.text.strip()
        match = re.search(r"(\[.*\])", text, re.DOTALL)
        return ast.literal_eval(match.group(1)) if match else []
        
    except Exception as e:
        st.error(f"Errore API ({model_name}): {e}")
        return []

# --- INTERFACCIA ---
st.title("🦁 MasterTb Manager")

# Sidebar Counter
with st.sidebar:
    st.header("🔢 Token")
    st.metric("Totale", st.session_state['token_usage']['total'])
    if st.button("Reset Token"):
        st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}
        st.rerun()

tab1, tab2, tab3 = st.tabs(["👁️ Cerca & Modifica", "➕ Nuovo Format", "📄 Doc AI"])

with tab1:
    # --- SEZIONE SCANNER MODELLI (CENTRALE) ---
    col_scan, col_sel = st.columns([1, 3])
    
    with col_scan:
        if st.button("🔍 Scansiona Modelli"):
            if "GOOGLE_API_KEY" in st.secrets:
                genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
                try:
                    # Trova modelli che supportano 'generateContent'
                    models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    if models:
                        st.session_state['available_models'] = models
                        st.toast(f"Trovati {len(models)} modelli!", icon="✅")
                    else:
                        st.error("Nessun modello trovato.")
                except Exception as e:
                    st.error(f"Errore Scan: {e}")
    
    with col_sel:
        # Il selectbox usa la lista nello stato, che si aggiorna con la scansione
        # Se il default non è nella lista scansionata, viene aggiunto o gestito
        index_default = 0
        if DEFAULT_MODEL in st.session_state['available_models']:
            index_default = st.session_state['available_models'].index(DEFAULT_MODEL)
            
        selected_model = st.selectbox(
            "Seleziona Modello AI", 
            st.session_state['available_models'],
            index=index_default
        )

    st.divider()

    # --- RICERCA ---
    with st.form("search_ai"):
        query = st.text_input("Cosa stai cercando?", placeholder="Es: attività all'aperto economiche")
        cerca_btn = st.form_submit_button("Cerca con Gemini 🦁")

    ids_to_show = product_ids
    
    if cerca_btn and query:
        with st.spinner(f"Sto chiedendo a {selected_model}..."):
            res = search_ai(query, df, selected_model)
            if res:
                ids_to_show = [x for x in res if x in product_ids]
                if not ids_to_show: st.warning("Trovati risultati ma non presenti nel DB attuale.")
            else:
                st.warning("Nessun risultato o errore API.")

    sel_id = st.selectbox(f"Seleziona {id_col}", ids_to_show)
    
    if sel_id:
        row = df.loc[sel_id]
        with st.form("edit"):
            st.markdown(f"### Modifica: {sel_id}")
            new_vals = {}
            for c in cols:
                val = str(row[c])
                if len(val) > 60: new_vals[c] = st.text_area(c, val)
                else: new_vals[c] = st.text_input(c, val)
            
            if st.form_submit_button("Salva Modifiche"):
                for c, v in new_vals.items():
                    if str(row[c]) != v:
                        r_idx = product_ids.index(sel_id) + 2 
                        c_idx = cols.index(c) + 1
                        ws.update_cell(r_idx, c_idx, v)
                        st.toast(f"Aggiornato {c}")
                load_data.clear()
                st.rerun()

with tab2:
    st.header("Nuovo")
    with st.form("new"):
        d = {}
        for c in cols:
            d[c] = st.text_input(c) if c == id_col else st.text_area(c)
        if st.form_submit_button("Crea"):
            if d[id_col] in product_ids: st.error("Esiste già")
            else:
                ws.append_row([d[c] for c in cols])
                st.success("Fatto")
                load_data.clear()
                st.rerun()

with tab3:
    st.info("Area PDF pronta.")
